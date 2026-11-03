"""Broadcast, variant, bundle, file-request and bulk-reprice writes."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from apexai_shop import (
    AsyncConfirmationRequired,
    AsyncPendingApproval,
    Completed,
    ConfirmationRequired,
    ConnectionError,
    PendingApproval,
    ServerError,
    models,
)
from conftest import PREFIX, SAMPLES, answer, make_async_shop, make_shop


def _body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _pending(reason: str = "owner_approval"):
    return answer(
        "pending_approval",
        approval={
            "id": 12,
            "status": "pending",
            "expires_at": "2026-11-03T12:15:00Z",
            "reasons": [reason],
        },
    )


def _reprice_preview():
    return answer(
        "confirmation_required",
        confirmation={"id": "conf_reprice1", "expires_at": "2026-11-03T12:05:00Z"},
        effect={
            "summary": "Change 1 item price.",
            "details": {"items": 1, "changed": 1},
        },
    )


def test_broadcast_send_is_pending_and_cancel_is_typed():
    shop, rec, _ = make_shop()
    pending = shop.broadcasts.send("A new guide is available.")
    cancelled = shop.broadcasts.cancel(30)

    assert isinstance(pending, PendingApproval)
    assert pending.approval.reasons == ["broadcast_requires_owner"]
    assert isinstance(cancelled, Completed)
    assert isinstance(cancelled.result, models.BroadcastInfo)
    assert cancelled.result.status == "cancelled"
    assert [(r.method, _path(r), _body(r)) for r in rec.requests] == [
        ("POST", "/broadcasts", {"text": "A new guide is available."}),
        ("POST", "/broadcasts/30/cancel", {}),
    ]


def test_variant_add_update_and_pending_price_cut():
    shop, rec, _ = make_shop()
    added = shop.variants.add(10, "Personal", 300)
    updated = shop.variants.update(
        20,
        version="0123456789abcdef",
        label="Commercial",
        price_stars=250,
        note="For one business.",
        active=True,
    )

    assert isinstance(added.result, models.Variant)
    assert added.result.version == "0123456789abcdef"
    assert updated.result.price_stars == 250
    assert [(r.method, _path(r)) for r in rec.requests] == [
        ("POST", "/items/10/variants"),
        ("PATCH", "/variants/20"),
    ]
    assert _body(rec.requests[1]) == {
        "version": "0123456789abcdef",
        "label": "Commercial",
        "price_stars": 250,
        "note": "For one business.",
        "active": True,
    }

    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending("deep_price_cut")))
    added_low = shop.variants.add(10, "Personal", 10)
    updated_low = shop.variants.update(20, version="0123456789abcdef", price_stars=10)
    assert isinstance(added_low, PendingApproval)
    assert isinstance(updated_low, PendingApproval)


def test_bundle_create_update_and_exactly_one_component_change():
    shop, rec, _ = make_shop()
    created = shop.bundles.create(1, "Starter pack", 500)
    updated = shop.bundles.update(11, add_item_id=10)

    assert isinstance(created.result, models.ItemDetail)
    assert created.result.delivery == "bundle"
    assert isinstance(updated.result, models.Bundle)
    assert updated.result.warning == "A bundle should contain at least two items."
    assert [_body(r) for r in rec.requests] == [
        {"category_id": 1, "title": "Starter pack", "price_stars": 500},
        {"add_item_id": 10},
    ]

    with pytest.raises(ValueError, match="exactly one"):
        shop.bundles.update(11)
    with pytest.raises(ValueError, match="exactly one"):
        shop.bundles.update(11, add_item_id=10, remove_item_id=9)
    assert len(rec.requests) == 2


def test_request_file_shape_and_transport_error_is_never_retried():
    shop, rec, _ = make_shop()
    requested = shop.items.request_file(10, variant_id=20)
    assert isinstance(requested.result, models.FileRequested)
    assert (requested.result.sent, requested.result.item_id) == (True, 10)
    assert _body(rec.requests[0]) == {"variant_id": 20}
    assert "idempotency-key" in rec.requests[0].headers

    shop, rec, sleeps = make_shop(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("lost")))
    with pytest.raises(ConnectionError) as info:
        shop.items.request_file(10)
    assert len(rec.requests) == 1
    assert sleeps == []
    assert info.value.idempotency_key == rec.requests[0].headers["idempotency-key"]


def test_request_file_new_call_gets_a_new_key():
    shop, rec, _ = make_shop()
    shop.items.request_file(10)
    shop.items.request_file(10)
    assert rec.requests[0].headers["idempotency-key"] != rec.requests[1].headers["idempotency-key"]


def test_request_file_never_retries_a_retryable_status():
    shop, rec, sleeps = make_shop(
        lambda request: httpx.Response(
            503,
            json={"error": "busy", "message": "Try later."},
            headers={"Retry-After": "1"},
        ),
        max_retries=5,
    )
    with pytest.raises(ServerError):
        shop.items.request_file(10)
    assert len(rec.requests) == 1
    assert sleeps == []


def test_reprice_is_two_step_and_serializes_changes():
    calls = []

    def handler(request):
        calls.append(_body(request))
        if "confirmation" not in calls[-1]:
            return httpx.Response(202, json=_reprice_preview())
        return httpx.Response(200, json=SAMPLES["reprice_items"])

    shop, _, _ = make_shop(handler)
    changes = [
        models.RepriceChange(
            item_id=10, version="0123456789abcdef", price_stars=250
        )
    ]
    preview = shop.items.reprice(changes)
    assert isinstance(preview, ConfirmationRequired)
    done = preview.confirm()
    assert isinstance(done, Completed)
    assert isinstance(done.result, models.RepriceResult)
    assert done.result.items[0].changed is True
    assert calls == [
        {
            "changes": [
                {
                    "item_id": 10,
                    "version": "0123456789abcdef",
                    "price_stars": 250,
                }
            ]
        },
        {
            "changes": [
                {
                    "item_id": 10,
                    "version": "0123456789abcdef",
                    "price_stars": 250,
                }
            ],
            "confirmation": "conf_reprice1",
        },
    ]


def test_reprice_deep_cut_can_wait_for_approval():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending("deep_price_cut")))
    result = shop.items.reprice(
        [models.RepriceChange(item_id=10, version="0123456789abcdef", price_stars=10)]
    )
    assert isinstance(result, PendingApproval)


def test_async_final_write_surface_parity():
    async def scenario():
        shop, rec, _ = make_async_shop()
        broadcast = await shop.broadcasts.send("News")
        cancelled = await shop.broadcasts.cancel(30)
        bundle = await shop.bundles.create(1, "Pack", 500)
        bundle_update = await shop.bundles.update(11, remove_item_id=10)
        variant = await shop.variants.add(10, "Personal", 300)
        variant_update = await shop.variants.update(
            20, version="0123456789abcdef", active=True
        )
        file_request = await shop.items.request_file(10)
        reprice = await shop.items.reprice(
            [
                models.RepriceChange(
                    item_id=10, version="0123456789abcdef", price_stars=250
                )
            ]
        )
        return (
            rec,
            broadcast,
            cancelled,
            bundle,
            bundle_update,
            variant,
            variant_update,
            file_request,
            reprice,
        )

    results = asyncio.run(scenario())
    rec, broadcast, cancelled, bundle, bundle_update, variant, variant_update, file_request, reprice = results
    assert isinstance(broadcast, AsyncPendingApproval)
    assert isinstance(cancelled.result, models.BroadcastInfo)
    assert isinstance(bundle.result, models.ItemDetail)
    assert isinstance(bundle_update.result, models.Bundle)
    assert isinstance(variant.result, models.Variant)
    assert isinstance(variant_update.result, models.Variant)
    assert isinstance(file_request.result, models.FileRequested)
    assert isinstance(reprice.result, models.RepriceResult)
    assert len(rec.requests) == 8


def test_async_reprice_confirmation_and_file_request_no_retry():
    calls = []

    def handler(request):
        calls.append(_body(request))
        if request.url.path.endswith("/reprice"):
            if "confirmation" not in calls[-1]:
                return httpx.Response(202, json=_reprice_preview())
            return httpx.Response(200, json=SAMPLES["reprice_items"])
        raise httpx.ReadTimeout("lost")

    async def scenario():
        shop, rec, sleeps = make_async_shop(handler)
        preview = await shop.items.reprice(
            [models.RepriceChange(item_id=10, version="0123456789abcdef", price_stars=250)]
        )
        assert isinstance(preview, AsyncConfirmationRequired)
        done = await preview.confirm()
        with pytest.raises(ConnectionError):
            await shop.items.request_file(10)
        return rec, sleeps, done

    rec, sleeps, done = asyncio.run(scenario())
    assert isinstance(done, Completed)
    assert len(rec.requests) == 3
    assert sleeps == []
