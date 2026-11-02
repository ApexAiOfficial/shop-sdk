"""Writes: request shape, idempotency keys, retries, typed results, confirmations and errors."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from apexai_shop import (
    ApprovalRequiredError,
    BadRequestError,
    ChangedError,
    Completed,
    ConfirmationInvalidError,
    ConfirmationRequired,
    ConflictError,
    ConnectionError,
    DailyCapError,
    NotFoundError,
    PendingApproval,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShopError,
    StoreSuspendedError,
    WriteResult,
    WritesDisabledError,
    models,
)
from apexai_shop._core import build_request
from conftest import (
    BASE_URL,
    CATEGORY,
    PREFIX,
    SAMPLES,
    TOKEN,
    answer,
    make_async_shop,
    make_shop,
    preview,
)

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _sequence(*responses):
    queue = list(responses)

    def handler(request):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def _error(status, code, headers=None, **extra):
    return httpx.Response(status, json={"error": code, "message": "Words.", **extra}, headers=headers)


def _done(op="create_category"):
    return httpx.Response(200, json=SAMPLES[op])


# --- request shape -----------------------------------------------------------


def test_create_category_request():
    shop, rec, _ = make_shop()
    result = shop.categories.create("Guides")
    request = rec.requests[0]
    assert request.method == "POST"
    assert _path(request) == "/categories"
    assert request.url.query == b""
    assert _body(request) == {"title": "Guides"}
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert KEY_RE.match(request.headers["idempotency-key"])
    assert isinstance(result, Completed)
    assert isinstance(result, WriteResult)
    assert isinstance(result.result, models.Category)
    assert result.result.title == "Guides"
    assert result.status == "done"
    assert not result.replayed and not result.noop


def test_update_category_sends_only_set_fields():
    shop, rec, _ = make_shop()
    result = shop.categories.update(1, title="Handbooks")
    shop.categories.update(1, active=False)
    shop.categories.update(1, title="Handbooks", active=True)
    assert [r.method for r in rec.requests] == ["PATCH"] * 3
    assert {_path(r) for r in rec.requests} == {"/categories/1"}
    assert [_body(r) for r in rec.requests] == [
        {"title": "Handbooks"},
        {"active": False},
        {"title": "Handbooks", "active": True},
    ]
    assert result.result.title == "Handbooks"


def test_move_category_request():
    shop, rec, _ = make_shop()
    result = shop.categories.move(1, 0)
    assert rec.requests[0].method == "POST"
    assert _path(rec.requests[0]) == "/categories/1/move"
    assert _body(rec.requests[0]) == {"to_index": 0}
    assert isinstance(result.result, models.CategoryMoved)
    assert (result.result.from_index, result.result.to_index) == (2, 0)


def test_delete_category_preview_sends_an_empty_object():
    shop, rec, _ = make_shop(lambda request: httpx.Response(202, json=preview()))
    shop.categories.delete(1)
    assert rec.requests[0].method == "POST"
    assert _path(rec.requests[0]) == "/categories/1/delete"
    assert _body(rec.requests[0]) == {}


def test_delete_category_with_explicit_confirmation():
    shop, rec, _ = make_shop()
    result = shop.categories.delete(1, confirmation="conf_abc123")
    assert _body(rec.requests[0]) == {"confirmation": "conf_abc123"}
    assert isinstance(result.result, models.CategoryDeleted)
    assert result.result.deleted is True


def test_write_path_params_are_escaped():
    shop, rec, _ = make_shop(lambda request: _error(404, "not_found"))
    with pytest.raises(NotFoundError):
        shop.categories.update("1/../../store", title="x")
    assert rec.requests[0].url.raw_path.startswith(b"/api/merchant/v1/categories/1%2F")


def test_reads_send_no_body_and_no_idempotency_key():
    shop, rec, _ = make_shop()
    shop.categories.list()
    assert rec.requests[0].content == b""
    assert "idempotency-key" not in rec.requests[0].headers


def test_build_request_refuses_unknown_body_fields():
    with pytest.raises(TypeError, match="no body field 'name'"):
        build_request(BASE_URL.rstrip("/"), "create_category", body={"name": "x"})
    with pytest.raises(TypeError, match="takes no body"):
        build_request(BASE_URL.rstrip("/"), "list_categories", body={"title": "x"})


# --- idempotency keys --------------------------------------------------------


def test_each_logical_write_gets_its_own_key():
    shop, rec, _ = make_shop()
    shop.categories.create("Guides")
    shop.categories.create("Guides")
    keys = [r.headers["idempotency-key"] for r in rec.requests]
    assert keys[0] != keys[1]


def test_caller_key_is_sent_verbatim():
    shop, rec, _ = make_shop()
    shop.categories.create("Guides", idempotency_key="order-sync-2026-11-02-0001")
    assert rec.requests[0].headers["idempotency-key"] == "order-sync-2026-11-02-0001"


@pytest.mark.parametrize(
    "key", ["short", "x" * 65, "has space in it!!", "ünïcode-key-0000001", "", "trailing-newline-01\n"]
)
def test_malformed_caller_key_is_refused_before_sending(key):
    shop, rec, _ = make_shop()
    with pytest.raises(ValueError, match="idempotency_key"):
        shop.categories.create("Guides", idempotency_key=key)
    assert rec.requests == []


def test_key_is_stable_across_a_retried_503_busy():
    shop, rec, sleeps = make_shop(
        _sequence(_error(503, "busy", {"Retry-After": "2"}), _error(503, "busy", {"Retry-After": "2"}), _done())
    )
    result = shop.categories.create("Guides")
    assert isinstance(result, Completed)
    assert len(rec.requests) == 3
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1
    assert {r.content for r in rec.requests} == {b'{"title":"Guides"}'}
    assert sleeps == [2.0, 2.0]


def test_key_is_stable_across_transport_errors():
    shop, rec, sleeps = make_shop(_sequence(httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), _done()))
    shop.categories.create("Guides", idempotency_key="my-own-key-000001")
    assert [r.headers["idempotency-key"] for r in rec.requests] == ["my-own-key-000001"] * 3
    assert sleeps == [0.5, 1.0]


def test_transport_errors_exhausted_raise_connection_error():
    shop, rec, _ = make_shop(_sequence(*[httpx.ConnectError("boom")] * 3))
    with pytest.raises(ConnectionError):
        shop.categories.create("Guides")
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


def test_replayed_answer():
    replay = answer(result=CATEGORY, replayed=True)
    shop, _, _ = make_shop(
        lambda request: httpx.Response(200, json=replay, headers={"Idempotent-Replayed": "true", "x-request-id": "r-9"})
    )
    result = shop.categories.create("Guides")
    assert isinstance(result, Completed)
    assert result.replayed is True
    assert result.request_id == "r-9"


def test_replayed_header_alone_marks_a_replay():
    shop, _, _ = make_shop(
        lambda request: httpx.Response(200, json=SAMPLES["create_category"], headers={"Idempotent-Replayed": "true"})
    )
    assert shop.categories.create("Guides").replayed is True


def test_noop_answer():
    shop, _, _ = make_shop(lambda request: httpx.Response(200, json=answer(result=CATEGORY, noop=True)))
    result = shop.categories.update(1, title="Guides")
    assert isinstance(result, Completed)
    assert result.noop is True
    assert result.result.title == "Guides"


# --- retries -----------------------------------------------------------------


def test_idempotency_in_progress_is_retried_after_retry_after():
    shop, rec, sleeps = make_shop(
        _sequence(_error(409, "idempotency_in_progress", {"Retry-After": "2"}), _done())
    )
    assert isinstance(shop.categories.create("Guides"), Completed)
    assert sleeps == [2.0]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


def test_idempotency_in_progress_exhausted_raises_conflict():
    resp = lambda: _error(409, "idempotency_in_progress", {"Retry-After": "2"})  # noqa: E731
    shop, rec, sleeps = make_shop(_sequence(resp(), resp(), resp()))
    with pytest.raises(ConflictError) as info:
        shop.categories.create("Guides")
    assert info.value.code == "idempotency_in_progress"
    assert len(rec.requests) == 3


def test_daily_cap_is_never_retried():
    shop, rec, sleeps = make_shop(
        _sequence(_error(429, "daily_cap", {"Retry-After": "3600"}), _done()), max_retries=5
    )
    with pytest.raises(DailyCapError) as info:
        shop.categories.create("Guides")
    assert isinstance(info.value, RateLimitError)
    assert info.value.retry_after == 3600.0
    assert len(rec.requests) == 1
    assert sleeps == []


def test_daily_cap_with_a_short_retry_after_is_still_not_retried():
    shop, rec, sleeps = make_shop(_sequence(_error(429, "daily_cap", {"Retry-After": "1"}), _done()))
    with pytest.raises(DailyCapError):
        shop.categories.create("Guides")
    assert len(rec.requests) == 1
    assert sleeps == []


def test_write_rate_limit_is_retried_with_the_same_key():
    shop, rec, sleeps = make_shop(_sequence(_error(429, "rate_limited", {"Retry-After": "1"}), _done()))
    assert isinstance(shop.categories.create("Guides"), Completed)
    assert sleeps == [1.0]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


def test_write_is_not_retried_when_retry_after_exceeds_the_cap():
    shop, rec, sleeps = make_shop(_sequence(_error(429, "rate_limited", {"Retry-After": "300"}), _done()))
    with pytest.raises(RateLimitError) as info:
        shop.categories.create("Guides")
    assert not isinstance(info.value, DailyCapError)
    assert info.value.retry_after == 300.0
    assert len(rec.requests) == 1
    assert sleeps == []


def test_gateway_errors_on_writes_back_off():
    shop, rec, sleeps = make_shop(_sequence(httpx.Response(502), httpx.Response(504), _done()))
    assert isinstance(shop.categories.create("Guides"), Completed)
    assert sleeps == [0.5, 1.0]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


def test_busy_exhausted_raises_server_error():
    resp = lambda: _error(503, "busy", {"Retry-After": "2"})  # noqa: E731
    shop, rec, _ = make_shop(_sequence(resp(), resp(), resp()))
    with pytest.raises(ServerError) as info:
        shop.categories.create("Guides")
    assert info.value.code == "busy"
    assert len(rec.requests) == 3


@pytest.mark.parametrize(
    "status,code",
    [(409, "conflict"), (409, "changed"), (409, "already_exists"), (423, "store_suspended"), (500, "internal_error")],
)
def test_final_write_errors_are_not_retried(status, code):
    shop, rec, sleeps = make_shop(_sequence(_error(status, code, {"Retry-After": "1"}), _done()))
    with pytest.raises(ShopError):
        shop.categories.create("Guides")
    assert len(rec.requests) == 1
    assert sleeps == []


def test_read_retries_are_unchanged():
    """A read still sleeps a capped Retry-After on 429, whatever the code."""
    shop, rec, sleeps = make_shop(
        _sequence(_error(429, "rate_limited", {"Retry-After": "120"}), httpx.Response(200, json=SAMPLES["get_store"]))
    )
    shop.store()
    assert sleeps == [30.0]


# --- confirmations -----------------------------------------------------------


def _confirm_flow(first, *then):
    """A handler answering the preview with ``first``, then each later request in turn."""
    return _sequence(first, *then)


def test_confirmation_round_trip():
    shop, rec, _ = make_shop(
        _confirm_flow(httpx.Response(202, json=preview()), _done("delete_category"))
    )
    first = shop.categories.delete(1)
    assert isinstance(first, ConfirmationRequired)
    assert first.status == "confirmation_required"
    assert first.result is None
    assert first.effect.summary == "Delete the category Guides."
    assert first.effect.details == {"category_id": 1, "title": "Guides"}
    assert first.confirmation.id == "conf_abc123"
    assert first.confirmation.expires_at.isoformat() == "2026-11-02T12:05:00+00:00"
    assert first.next.startswith("Show the user")
    assert "conf_abc123" not in repr(first)

    done = first.confirm()
    assert isinstance(done, Completed)
    assert done.result == models.CategoryDeleted(deleted=True, category_id=1)
    preview_request, confirm_request = rec.requests
    assert (confirm_request.method, _path(confirm_request)) == ("POST", "/categories/1/delete")
    assert _body(confirm_request) == {"confirmation": "conf_abc123"}
    # The confirming call is its own logical write, with its own key.
    assert KEY_RE.match(confirm_request.headers["idempotency-key"])
    assert confirm_request.headers["idempotency-key"] != preview_request.headers["idempotency-key"]


def test_confirm_keeps_its_key_across_retries_and_accepts_a_caller_key():
    shop, rec, sleeps = make_shop(
        _confirm_flow(
            httpx.Response(202, json=preview()),
            _error(503, "busy", {"Retry-After": "2"}),
            _done("delete_category"),
        )
    )
    shop.categories.delete(1).confirm(idempotency_key="confirm-key-00000001")
    assert [r.headers["idempotency-key"] for r in rec.requests[1:]] == ["confirm-key-00000001"] * 2
    assert sleeps == [2.0]


def test_confirm_on_a_write_without_a_confirmation_field_is_refused():
    shop, rec, _ = make_shop(
        _confirm_flow(httpx.Response(202, json=preview()), _done("update_category"))
    )
    # update_category takes no confirmation in the contract: nothing is sent.
    first = shop.categories.update(1, title="Handbooks", active=False)
    assert isinstance(first, ConfirmationRequired)
    with pytest.raises(TypeError, match="no body field 'confirmation'"):
        first.confirm()
    assert len(rec.requests) == 1


def test_changed_raises_with_a_fresh_bound_preview():
    fresh = preview(confirmation_id="conf_fresh456", summary="Delete the category Guides (3 hidden items).")
    shop, rec, _ = make_shop(
        _confirm_flow(
            httpx.Response(202, json=preview()),
            _error(409, "changed", preview=fresh),
            _done("delete_category"),
        )
    )
    first = shop.categories.delete(1)
    with pytest.raises(ChangedError) as info:
        first.confirm()
    err = info.value
    assert isinstance(err, ConflictError)
    assert err.status == 409 and err.code == "changed"
    assert isinstance(err.preview, ConfirmationRequired)
    assert err.preview.confirmation.id == "conf_fresh456"
    assert err.preview.effect.summary.endswith("(3 hidden items).")
    assert len(rec.requests) == 2  # a changed effect is never retried

    done = err.preview.confirm()
    assert isinstance(done, Completed)
    assert _body(rec.requests[2]) == {"confirmation": "conf_fresh456"}
    assert _path(rec.requests[2]) == "/categories/1/delete"


def test_changed_without_a_usable_preview():
    shop, _, _ = make_shop(lambda request: _error(409, "changed", preview={"status": "done"}))
    with pytest.raises(ChangedError) as info:
        shop.categories.delete(1, confirmation="conf_abc123")
    assert info.value.preview is None


def test_confirmation_invalid():
    shop, _, _ = make_shop(lambda request: _error(409, "confirmation_invalid"))
    with pytest.raises(ConfirmationInvalidError) as info:
        shop.categories.delete(1, confirmation="conf_old")
    assert isinstance(info.value, ConflictError)


def test_pending_approval():
    pending = answer(
        "pending_approval",
        approval={"id": 12, "status": "pending", "expires_at": "2026-11-02T12:15:00Z"},
        effect={"summary": "Rename the category.", "details": {}},
        next="Ask the store owner to tap Approve in the bot's private chat.",
    )
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=pending))
    result = shop.categories.update(1, title="Handbooks")
    assert isinstance(result, PendingApproval)
    assert result.approval.id == 12
    assert result.approval.status == "pending"
    assert result.result is None
    assert not hasattr(result, "confirm")


def test_confirmation_required_without_confirmation_is_invalid():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=answer("confirmation_required")))
    with pytest.raises(ShopError) as info:
        shop.categories.delete(1)
    assert info.value.code == "invalid_response"


def test_unknown_status_is_a_plain_write_result():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=answer("queued")))
    result = shop.categories.create("Guides")
    assert type(result) is WriteResult
    assert result.status == "queued"


# --- errors ------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,code,cls",
    [
        (400, "nothing_to_change", BadRequestError),
        (400, "not_empty", BadRequestError),
        (403, "insufficient_scope", PermissionDeniedError),
        (403, "writes_disabled", WritesDisabledError),
        (403, "approval_required", ApprovalRequiredError),
        (404, "not_found", NotFoundError),
        (409, "conflict", ConflictError),
        (409, "already_exists", ConflictError),
        (409, "confirmation_invalid", ConfirmationInvalidError),
        (409, "changed", ChangedError),
        (422, "idempotency_key_reused", BadRequestError),
        (423, "store_suspended", StoreSuspendedError),
        (423, "something_new", StoreSuspendedError),
        (429, "daily_cap", DailyCapError),
        (503, "telegram_unavailable", ServerError),
    ],
)
def test_write_error_mapping(status, code, cls):
    shop, _, _ = make_shop(lambda request: _error(status, code, {"x-request-id": "req-7"}), max_retries=0)
    with pytest.raises(cls) as info:
        shop.categories.create("Guides")
    err = info.value
    assert type(err) is cls
    assert (err.status, err.code, err.message, err.request_id) == (status, code, "Words.", "req-7")


def test_insufficient_scope_is_not_a_write_subclass():
    shop, _, _ = make_shop(lambda request: _error(403, "insufficient_scope"))
    with pytest.raises(PermissionDeniedError) as info:
        shop.categories.create("Guides")
    assert not isinstance(info.value, (ApprovalRequiredError, WritesDisabledError))


def test_conflict_carries_current():
    current = dict(CATEGORY, title="Renamed elsewhere")
    shop, _, _ = make_shop(lambda request: _error(409, "conflict", current=current))
    with pytest.raises(ConflictError) as info:
        shop.categories.update(1, title="Handbooks")
    assert info.value.current == current


def test_other_conflicts_have_no_current():
    shop, _, _ = make_shop(lambda request: _error(409, "already_exists"))
    with pytest.raises(ConflictError) as info:
        shop.categories.create("Guides")
    assert info.value.current is None


def test_approval_required_carries_reasons():
    reasons = ["The price drops by more than half."]
    shop, _, _ = make_shop(lambda request: _error(403, "approval_required", reasons=reasons))
    with pytest.raises(ApprovalRequiredError) as info:
        shop.categories.update(1, title="x")
    assert isinstance(info.value, PermissionDeniedError)
    assert info.value.reasons == reasons
    assert "more than half" in str(info.value)


def test_write_errors_without_a_message_get_a_default():
    shop, _, _ = make_shop(lambda request: httpx.Response(423, json={"error": "store_suspended"}))
    with pytest.raises(StoreSuspendedError) as info:
        shop.categories.create("Guides")
    assert info.value.message == "The store is suspended."


def test_422_write_fields():
    shop, _, _ = make_shop(lambda request: _error(422, "invalid_request", fields=["title"]))
    with pytest.raises(BadRequestError) as info:
        shop.categories.create("")
    assert info.value.fields == ["title"]


def test_token_never_in_write_results_or_errors():
    shop, _, _ = make_shop(_sequence(httpx.Response(202, json=preview()), _error(409, "changed", preview=preview())))
    first = shop.categories.delete(1)
    with pytest.raises(ChangedError) as info:
        first.confirm()
    for text in (repr(first), str(info.value), repr(info.value), repr(info.value.preview)):
        assert TOKEN not in text


# --- async parity ------------------------------------------------------------


def test_async_category_writes_hit_their_paths():
    async def main():
        shop, rec, _ = make_async_shop()
        async with shop:
            created = await shop.categories.create("Guides")
            updated = await shop.categories.update(1, active=False)
            moved = await shop.categories.move(1, to_index=0)
            deleted = await shop.categories.delete(1, confirmation="conf_abc123")
        return rec, (created, updated, moved, deleted)

    rec, results = asyncio.run(main())
    assert [(r.method, _path(r), _body(r)) for r in rec.requests] == [
        ("POST", "/categories", {"title": "Guides"}),
        ("PATCH", "/categories/1", {"active": False}),
        ("POST", "/categories/1/move", {"to_index": 0}),
        ("POST", "/categories/1/delete", {"confirmation": "conf_abc123"}),
    ]
    assert all(isinstance(r, Completed) for r in results)
    assert isinstance(results[2].result, models.CategoryMoved)
    assert all(KEY_RE.match(r.headers["idempotency-key"]) for r in rec.requests)


def test_async_key_is_stable_across_busy_and_daily_cap_is_final():
    async def main():
        shop, rec, sleeps = make_async_shop(
            _sequence(
                _error(503, "busy", {"Retry-After": "2"}),
                _done(),
                _error(429, "daily_cap", {"Retry-After": "60"}),
            )
        )
        result = await shop.categories.create("Guides")
        with pytest.raises(DailyCapError):
            await shop.categories.create("Guides")
        return rec, sleeps, result

    rec, sleeps, result = asyncio.run(main())
    assert isinstance(result, Completed)
    assert sleeps == [2.0]
    assert rec.requests[0].headers["idempotency-key"] == rec.requests[1].headers["idempotency-key"]
    assert rec.requests[2].headers["idempotency-key"] != rec.requests[1].headers["idempotency-key"]
    assert len(rec.requests) == 3


def test_async_confirmation_round_trip_and_changed():
    fresh = preview(confirmation_id="conf_fresh456")

    async def main():
        shop, rec, _ = make_async_shop(
            _sequence(
                httpx.Response(202, json=preview()),
                _error(409, "changed", preview=fresh),
                _done("delete_category"),
            )
        )
        first = await shop.categories.delete(1)
        assert isinstance(first, ConfirmationRequired)
        try:
            await first.confirm()
        except ChangedError as exc:
            done = await exc.preview.confirm()
        else:  # pragma: no cover
            raise AssertionError("expected ChangedError")
        return rec, done

    rec, done = asyncio.run(main())
    assert isinstance(done, Completed)
    assert done.result.deleted is True
    assert [_body(r) for r in rec.requests] == [
        {},
        {"confirmation": "conf_abc123"},
        {"confirmation": "conf_fresh456"},
    ]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 3


def test_async_malformed_key_is_refused_before_sending():
    async def main():
        shop, rec, _ = make_async_shop()
        with pytest.raises(ValueError):
            await shop.categories.create("Guides", idempotency_key="nope")
        return rec

    assert asyncio.run(main()).requests == []
