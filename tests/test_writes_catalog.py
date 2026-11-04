"""Item and coupon writes, and the coupon list: request shape and typed results, sync and async."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from apexai_shop import (
    ApprovalRequiredError,
    ChangedError,
    Completed,
    ConfirmationRequired,
    ConflictError,
    models,
)
from conftest import PREFIX, SAMPLES, answer, make_async_shop, make_shop


def _body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _error(status, code, **extra):
    return httpx.Response(status, json={"error": code, "message": "Words.", **extra})


def _coupon_preview():
    return answer(
        "confirmation_required",
        confirmation={"id": "conf_coupon01", "expires_at": "2026-11-02T12:05:00Z"},
        effect={
            "summary": "Switch off SPRING10, which 3 paid orders used.",
            "details": {"in_use": True, "before": {"is_active": True}, "after": {"is_active": False}},
        },
        next="Show the user this effect; call update_coupon again with this confirmation only after they agree.",
    )


# --- items -------------------------------------------------------------------


def test_create_item_request_and_result():
    shop, rec, _ = make_shop()
    result = shop.items.create(1, "Guide", 300, description="A short guide.")
    request = rec.requests[0]
    assert (request.method, _path(request)) == ("POST", "/items")
    assert _body(request) == {
        "category_id": 1,
        "title": "Guide",
        "description": "A short guide.",
        "price_stars": 300,
    }
    assert "idempotency-key" in request.headers
    assert isinstance(result, Completed)
    assert isinstance(result.result, models.ItemDetail)
    assert result.result.status == "hidden"


def test_create_item_without_description_leaves_it_to_the_server():
    shop, rec, _ = make_shop()
    shop.items.create(1, "Guide", 300)
    assert _body(rec.requests[0]) == {"category_id": 1, "title": "Guide", "price_stars": 300}


def test_update_item_sends_version_and_only_set_fields():
    shop, rec, _ = make_shop()
    result = shop.items.update(10, version="0123456789abcdef", price_stars=250)
    shop.items.update(10, active=True)
    assert [(r.method, _path(r)) for r in rec.requests] == [("PATCH", "/items/10")] * 2
    assert [_body(r) for r in rec.requests] == [
        {"version": "0123456789abcdef", "price_stars": 250},
        {"active": True},
    ]
    assert result.result.price_stars == 250


def test_update_item_conflict_carries_the_current_item():
    current = dict(SAMPLES["get_item"], title="Renamed elsewhere")
    shop, _, _ = make_shop(lambda request: _error(409, "conflict", current=current))
    with pytest.raises(ConflictError) as info:
        shop.items.update(10, version="0123456789abcdef", title="Mine")
    assert info.value.code == "conflict"
    assert info.value.current["title"] == "Renamed elsewhere"
    assert info.value.idempotency_key


def test_deep_price_cut_needs_approval():
    shop, _, _ = make_shop(
        lambda request: _error(403, "approval_required", reasons=["The price falls by more than half."])
    )
    with pytest.raises(ApprovalRequiredError) as info:
        shop.items.update(10, version="0123456789abcdef", price_stars=10)
    assert info.value.reasons == ["The price falls by more than half."]


def test_move_item_request():
    shop, rec, _ = make_shop()
    result = shop.items.move(10, 0)
    assert (rec.requests[0].method, _path(rec.requests[0])) == ("POST", "/items/10/move")
    assert _body(rec.requests[0]) == {"to_index": 0}
    assert isinstance(result.result, models.ItemMoved)
    assert (result.result.from_index, result.result.to_index) == (3, 0)


def test_delete_item_two_steps():
    calls = []

    def handler(request):
        calls.append(_body(request))
        if "confirmation" not in calls[-1]:
            return httpx.Response(202, json=answer(
                "confirmation_required",
                confirmation={"id": "conf_item0001", "expires_at": "2026-11-02T12:05:00Z"},
                effect={"summary": "Archive the item Guide.", "details": {"outcome": "archived", "carts": 2}},
            ))
        return httpx.Response(200, json=SAMPLES["delete_item"])

    shop, rec, _ = make_shop(handler)
    preview = shop.items.delete(10)
    assert isinstance(preview, ConfirmationRequired)
    assert preview.effect.details["carts"] == 2
    done = preview.confirm()
    assert calls == [{}, {"confirmation": "conf_item0001"}]
    assert {_path(r) for r in rec.requests} == {"/items/10/delete"}
    assert isinstance(done.result, models.ItemDeleted)
    assert done.result.outcome == "archived"


def test_delete_item_changed_gives_a_fresh_preview():
    fresh = answer(
        "confirmation_required",
        confirmation={"id": "conf_item0002", "expires_at": "2026-11-02T12:06:00Z"},
        effect={"summary": "Archive the item Guide (now in a bundle).", "details": {"outcome": "archived_in_bundles"}},
    )
    shop, _, _ = make_shop(lambda request: _error(409, "changed", preview=fresh))
    with pytest.raises(ChangedError) as info:
        shop.items.delete(10, confirmation="conf_item0001")
    assert isinstance(info.value.preview, ConfirmationRequired)
    assert info.value.preview.effect.details["outcome"] == "archived_in_bundles"


# --- coupons -----------------------------------------------------------------


def test_list_coupons_query_and_model():
    shop, rec, _ = make_shop()
    page = shop.coupons.list(status="active", code="spring10", limit=10)
    request = rec.requests[0]
    assert (request.method, _path(request)) == ("GET", "/coupons")
    assert dict(request.url.params) == {"status": "active", "code": "spring10", "limit": "10"}
    assert "idempotency-key" not in request.headers
    coupon = page.data[0]
    assert isinstance(coupon, models.Coupon)
    assert (coupon.code, coupon.uses.paid, coupon.uses.open_checkouts) == ("SPRING10", 3, 1)


def test_iter_coupons_follows_the_cursor():
    pages = [
        {"data": [SAMPLES["list_coupons"]["data"][0]], "next_cursor": "c2"},
        {"data": [dict(SAMPLES["list_coupons"]["data"][0], code="AUTUMN5")], "next_cursor": None},
    ]
    shop, rec, _ = make_shop(lambda request: httpx.Response(200, json=pages.pop(0)))
    codes = [c.code for c in shop.coupons.iter(status="all")]
    assert codes == ["SPRING10", "AUTUMN5"]
    assert dict(rec.requests[1].url.params) == {"cursor": "c2"}


def test_create_coupon_request_encodes_the_expiry():
    shop, rec, _ = make_shop()
    expires = datetime(2026, 12, 1, tzinfo=timezone.utc)
    result = shop.coupons.create("spring10", 10, max_uses=100, per_user_max_uses=1, expires_at=expires)
    request = rec.requests[0]
    assert (request.method, _path(request)) == ("POST", "/coupons")
    assert _body(request) == {
        "code": "spring10",
        "pct_off": 10,
        "max_uses": 100,
        "per_user_max_uses": 1,
        "expires_at": "2026-12-01T00:00:00+00:00",
    }
    assert isinstance(result.result, models.Coupon)


def test_create_coupon_without_limits_sends_only_code_and_percent():
    shop, rec, _ = make_shop()
    shop.coupons.create("SPRING10", 10)
    assert _body(rec.requests[0]) == {"code": "SPRING10", "pct_off": 10}


def test_update_coupon_quotes_the_code_in_the_path():
    shop, rec, _ = make_shop(lambda request: httpx.Response(200, json=SAMPLES["update_coupon"]))
    shop.coupons.update("A-B_c", max_uses=200)
    assert _path(rec.requests[0]) == "/coupons/A-B_c"
    assert _body(rec.requests[0]) == {"max_uses": 200}


def test_switching_off_a_used_coupon_confirms_first():
    calls = []

    def handler(request):
        calls.append(_body(request))
        if "confirmation" not in calls[-1]:
            return httpx.Response(202, json=_coupon_preview())
        return httpx.Response(200, json=SAMPLES["update_coupon"])

    shop, _, _ = make_shop(handler)
    preview = shop.coupons.update("SPRING10", active=False)
    assert isinstance(preview, ConfirmationRequired)
    done = preview.confirm()
    assert calls == [{"active": False}, {"active": False, "confirmation": "conf_coupon01"}]
    assert isinstance(done, Completed)
    assert done.result.is_active is False


# --- async parity ------------------------------------------------------------


def test_async_item_and_coupon_writes():
    async def scenario():
        shop, rec, _ = make_async_shop()
        created = await shop.items.create(1, "Guide", 300)
        updated = await shop.items.update(10, version="0123456789abcdef", price_stars=250)
        moved = await shop.items.move(10, 0)
        coupon = await shop.coupons.create("SPRING10", 10)
        page = await shop.coupons.list()
        codes = [c.code async for c in shop.coupons.iter()]
        return rec, created, updated, moved, coupon, page, codes

    rec, created, updated, moved, coupon, page, codes = asyncio.run(scenario())
    assert [(r.method, _path(r)) for r in rec.requests[:4]] == [
        ("POST", "/items"),
        ("PATCH", "/items/10"),
        ("POST", "/items/10/move"),
        ("POST", "/coupons"),
    ]
    assert isinstance(created.result, models.ItemDetail)
    assert updated.result.price_stars == 250
    assert isinstance(moved.result, models.ItemMoved)
    assert isinstance(coupon.result, models.Coupon)
    assert page.data[0].code == "SPRING10"
    assert codes == ["SPRING10"]


def test_async_delete_item_confirm_is_awaited():
    calls = []

    def handler(request):
        calls.append(_body(request))
        if "confirmation" not in calls[-1]:
            return httpx.Response(202, json=answer(
                "confirmation_required",
                confirmation={"id": "conf_item0001", "expires_at": "2026-11-02T12:05:00Z"},
                effect={"summary": "Delete the item Guide.", "details": {"outcome": "deleted"}},
            ))
        return httpx.Response(200, json=answer(result={"item_id": 10, "outcome": "deleted", "bundle_ids": []}))

    async def scenario():
        shop, _, _ = make_async_shop(handler)
        preview = await shop.items.delete(10)
        return await preview.confirm()

    done = asyncio.run(scenario())
    assert calls == [{}, {"confirmation": "conf_item0001"}]
    assert done.result.outcome == "deleted"
