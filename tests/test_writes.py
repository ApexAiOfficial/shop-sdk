"""Writes: request shape, idempotency keys, retries, typed results, confirmations and errors."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from apexai_shop import (
    AsyncConfirmationRequired,
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
from apexai_shop._core import LOST_ANSWER_NOTE, build_request
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
    assert sleeps == [2.0, 2.0]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


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


@pytest.mark.parametrize(
    "status,code,cls",
    [(409, "idempotency_in_progress", ConflictError), (503, "busy", ServerError)],
)
def test_write_retry_after_over_the_cap_is_final(status, code, cls):
    shop, rec, sleeps = make_shop(_sequence(_error(status, code, {"Retry-After": "300"}), _done()))
    with pytest.raises(cls) as info:
        shop.categories.create("Guides")
    assert info.value.code == code
    assert len(rec.requests) == 1
    assert sleeps == []


def test_write_rate_limit_without_retry_after_backs_off():
    shop, rec, sleeps = make_shop(_sequence(_error(429, "rate_limited"), _error(429, "rate_limited"), _done()))
    assert isinstance(shop.categories.create("Guides"), Completed)
    assert sleeps == [0.5, 1.0]
    assert len({r.headers["idempotency-key"] for r in rec.requests}) == 1


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
    [
        (409, "conflict"),
        (409, "changed"),
        (409, "confirmation_invalid"),
        (409, "a_code_newer_than_this_client"),
        (423, "store_suspended"),
        (500, "internal_error"),
    ],
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
    for text in (repr(first), repr(first.confirmation), repr(vars(first)), repr(first.answer)):
        assert "conf_abc123" not in text
    # The envelope's dump is data, for logging on purpose or returning to an agent: it has the id.
    assert first.answer.model_dump(mode="json")["confirmation"]["id"] == "conf_abc123"

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
    with pytest.raises(ShopError) as info:
        first.confirm()
    assert info.value.code == "invalid_response"
    assert "cannot confirm" in info.value.message
    assert "update_category" not in str(info.value)
    assert len(rec.requests) == 1


def test_changed_preview_of_a_write_without_a_confirmation_field_cannot_confirm():
    """The shared 409 description lists ``changed`` for every write; only two-step writes can confirm."""
    shop, rec, _ = make_shop(lambda request: _error(409, "changed", preview=preview()))
    with pytest.raises(ChangedError) as info:
        shop.categories.update(1, title="x")
    assert isinstance(info.value.preview, ConfirmationRequired)
    with pytest.raises(ShopError) as refused:
        info.value.preview.confirm()
    assert refused.value.code == "invalid_response"
    assert len(rec.requests) == 1


def test_delete_accepts_the_confirmation_object():
    shop, rec, _ = make_shop(_confirm_flow(httpx.Response(202, json=preview()), _done("delete_category")))
    first = shop.categories.delete(1)
    done = shop.categories.delete(1, confirmation=first.confirmation)
    assert isinstance(done, Completed)
    assert _body(rec.requests[1]) == {"confirmation": "conf_abc123"}


def test_confirm_again_after_a_lost_answer_resends_the_confirmation():
    """A confirmation that made its change answers the stored result to any later confirm."""
    replay = answer(result={"deleted": True, "category_id": 1}, replayed=True)
    shop, rec, _ = make_shop(
        _confirm_flow(
            httpx.Response(202, json=preview()),
            *[httpx.ReadTimeout("lost")] * 3,
            httpx.Response(200, json=replay, headers={"Idempotent-Replayed": "true"}),
            httpx.Response(200, json=replay, headers={"Idempotent-Replayed": "true"}),
        )
    )
    first = shop.categories.delete(1)
    with pytest.raises(ConnectionError) as info:
        first.confirm()
    lost_key = info.value.idempotency_key
    assert [r.headers["idempotency-key"] for r in rec.requests[1:4]] == [lost_key] * 3

    again = first.confirm(idempotency_key=lost_key)
    assert isinstance(again, Completed) and again.replayed
    plain = first.confirm()
    assert isinstance(plain, Completed) and plain.replayed
    assert rec.requests[4].headers["idempotency-key"] == lost_key
    assert rec.requests[5].headers["idempotency-key"] != lost_key
    assert [_body(r) for r in rec.requests[1:]] == [{"confirmation": "conf_abc123"}] * 5


@pytest.mark.parametrize(
    "lost,noted",
    [
        (httpx.ReadTimeout("answer lost"), True),
        (httpx.RemoteProtocolError("reset"), True),
        (httpx.Response(504), True),
        (_error(409, "idempotency_in_progress", {"Retry-After": "1"}), True),
        (httpx.ConnectError("never sent"), False),
        (_error(503, "busy", {"Retry-After": "1"}), False),
    ],
)
def test_confirmation_invalid_after_a_lost_answer_says_so(lost, noted):
    shop, rec, _ = make_shop(
        _confirm_flow(httpx.Response(202, json=preview()), lost, _error(409, "confirmation_invalid"))
    )
    first = shop.categories.delete(1)
    with pytest.raises(ConfirmationInvalidError) as info:
        first.confirm()
    assert (LOST_ANSWER_NOTE in info.value.message) is noted
    assert info.value.message.startswith("Words.")
    assert len(rec.requests) == 3


def test_confirmation_invalid_on_a_first_attempt_has_no_note():
    shop, _, _ = make_shop(lambda request: _error(409, "confirmation_invalid"))
    with pytest.raises(ConfirmationInvalidError) as info:
        shop.categories.delete(1, confirmation="conf_old")
    assert info.value.message == "Words."


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


def test_confirmation_required_without_effect_is_invalid():
    body = dict(preview(), effect=None)
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=body))
    with pytest.raises(ShopError) as info:
        shop.categories.delete(1)
    assert info.value.code == "invalid_response"
    assert "without effect" in info.value.message


def test_done_without_result_is_invalid_and_carries_the_key():
    shop, rec, _ = make_shop(lambda request: httpx.Response(200, json=answer()))
    with pytest.raises(ShopError) as info:
        shop.categories.create("Guides")
    assert info.value.code == "invalid_response"
    assert "the change was made" in info.value.message
    assert info.value.idempotency_key == rec.requests[0].headers["idempotency-key"]


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
        (409, "idempotency_in_progress", ConflictError),
        (409, "confirmation_invalid", ConfirmationInvalidError),
        (409, "changed", ChangedError),
        (422, "idempotency_key_reused", BadRequestError),
        (423, "store_suspended", StoreSuspendedError),
        (429, "rate_limited", RateLimitError),
        (429, "daily_cap", DailyCapError),
        (503, "busy", ServerError),
        # Codes newer than this client map by status.
        (409, "a_code_newer_than_this_client", ConflictError),
        (423, "a_code_newer_than_this_client", StoreSuspendedError),
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
    shop, _, _ = make_shop(lambda request: _error(409, "a_code_newer_than_this_client"))
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


def test_token_and_confirmation_ids_never_in_write_results_or_errors():
    fresh = preview(confirmation_id="conf_fresh456")
    shop, _, _ = make_shop(_sequence(httpx.Response(202, json=preview()), _error(409, "changed", preview=fresh)))
    first = shop.categories.delete(1)
    with pytest.raises(ChangedError) as info:
        first.confirm()
    err = info.value
    texts = (
        repr(first),
        repr(first.confirmation),
        str(err),
        repr(err),
        repr(err.preview),
        repr(err.preview.confirmation),
        repr(vars(err.preview)),
    )
    for text in texts:
        assert TOKEN not in text
        assert "conf_abc123" not in text and "conf_fresh456" not in text


# --- the idempotency key on errors -------------------------------------------


def test_connection_error_carries_the_key_and_a_retry_with_it_sends_the_same_header():
    shop, rec, _ = make_shop(_sequence(*[httpx.ReadTimeout("lost")] * 3, _done()))
    with pytest.raises(ConnectionError) as info:
        shop.categories.create("Guides")
    key = info.value.idempotency_key
    assert key is not None and KEY_RE.match(key)
    assert {r.headers["idempotency-key"] for r in rec.requests} == {key}

    result = shop.categories.create("Guides", idempotency_key=key)
    assert isinstance(result, Completed)
    assert rec.requests[3].headers["idempotency-key"] == key
    assert rec.requests[3].content == rec.requests[0].content


@pytest.mark.parametrize(
    "response",
    [
        _error(500, "internal_error"),
        _error(409, "conflict"),
        _error(429, "daily_cap", {"Retry-After": "60"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_every_write_error_carries_the_key(response):
    shop, rec, _ = make_shop(lambda request: response)
    with pytest.raises(ShopError) as info:
        shop.categories.create("Guides", idempotency_key="caller-key-0000001")
    assert info.value.idempotency_key == "caller-key-0000001"
    assert "caller-key-0000001" not in repr(info.value)


def test_read_errors_have_no_key():
    shop, _, _ = make_shop(lambda request: _error(404, "not_found"))
    with pytest.raises(NotFoundError) as info:
        shop.items.get(1)
    assert info.value.idempotency_key is None


def test_confirm_errors_carry_the_confirming_calls_key():
    shop, rec, _ = make_shop(
        _confirm_flow(httpx.Response(202, json=preview()), _error(409, "changed", preview=preview()))
    )
    first = shop.categories.delete(1)
    with pytest.raises(ChangedError) as info:
        first.confirm(idempotency_key="confirm-key-00000001")
    assert info.value.idempotency_key == "confirm-key-00000001"
    assert rec.requests[0].headers["idempotency-key"] != "confirm-key-00000001"


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
        assert isinstance(first, AsyncConfirmationRequired)
        assert isinstance(first, ConfirmationRequired)
        try:
            await first.confirm()
        except ChangedError as exc:
            assert isinstance(exc.preview, AsyncConfirmationRequired)
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


def test_async_connection_error_carries_the_key():
    async def main():
        shop, rec, _ = make_async_shop(_sequence(*[httpx.ReadTimeout("lost")] * 3, _done()))
        with pytest.raises(ConnectionError) as info:
            await shop.categories.create("Guides")
        key = info.value.idempotency_key
        result = await shop.categories.create("Guides", idempotency_key=key)
        return rec, key, result

    rec, key, result = asyncio.run(main())
    assert isinstance(result, Completed)
    assert [r.headers["idempotency-key"] for r in rec.requests] == [key] * 4


def test_async_lost_answer_note_and_unconfirmable_preview():
    async def main():
        shop, rec, _ = make_async_shop(
            _sequence(
                httpx.Response(202, json=preview()),
                httpx.ReadTimeout("lost"),
                _error(409, "confirmation_invalid"),
                httpx.Response(202, json=preview()),
            )
        )
        first = await shop.categories.delete(1)
        with pytest.raises(ConfirmationInvalidError) as info:
            await first.confirm()
        update = await shop.categories.update(1, title="x")
        assert isinstance(update, AsyncConfirmationRequired)
        with pytest.raises(ShopError) as refused:
            await update.confirm()
        return rec, info.value, refused.value

    rec, invalid, refused = asyncio.run(main())
    assert LOST_ANSWER_NOTE in invalid.message
    assert invalid.idempotency_key == rec.requests[1].headers["idempotency-key"]
    assert refused.code == "invalid_response"
    assert len(rec.requests) == 4


def test_an_undecodable_answer_is_retried_with_the_same_key_and_carries_it():
    keys = []

    def handler(request):
        keys.append(request.headers["idempotency-key"])
        raise httpx.DecodingError("Error -3 while decompressing data", request=request)

    shop, _, _ = make_shop(handler, max_retries=1)
    with pytest.raises(ConnectionError) as info:
        shop.categories.create("Guides")
    assert len(keys) == 2 and len(set(keys)) == 1
    assert info.value.idempotency_key == keys[0]
    assert "DecodingError" in info.value.message


def test_async_an_undecodable_answer_carries_the_key():
    keys = []

    def handler(request):
        keys.append(request.headers["idempotency-key"])
        raise httpx.DecodingError("Error -3 while decompressing data", request=request)

    async def scenario():
        shop, _, _ = make_async_shop(handler, max_retries=0)
        with pytest.raises(ConnectionError) as info:
            await shop.categories.create("Guides")
        return info.value

    err = asyncio.run(scenario())
    assert err.idempotency_key == keys[0]
