"""Support tickets and download restoration, sync and async."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from apexai_shop import (
    ApprovalRequiredError,
    BadRequestError,
    Completed,
    DailyCapError,
    models,
)
from conftest import PREFIX, SAMPLES, make_async_shop, make_shop


def _body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _error(status: int, code: str, **extra):
    return httpx.Response(status, json={"error": code, "message": "Refused.", **extra})


def test_list_and_iter_tickets_send_filters_and_parse_untrusted_text():
    shop, rec, _ = make_shop()
    page = shop.tickets.list(include_closed=False, limit=10)
    tickets = list(shop.tickets.iter(include_closed=True, limit=5))

    assert [(request.method, _path(request)) for request in rec.requests] == [
        ("GET", "/tickets"),
        ("GET", "/tickets"),
    ]
    assert [dict(request.url.params) for request in rec.requests] == [
        {"include_closed": "false", "limit": "10"},
        {"include_closed": "true", "limit": "5"},
    ]
    assert isinstance(page, models.TicketPage)
    assert page.untrusted_fields == ["subject"]
    assert isinstance(tickets[0], models.TicketSummary)
    assert tickets[0].subject == "My download expired"


def test_get_ticket_models_thread_and_redacted_buyer_ids():
    shop, rec, _ = make_shop()
    ticket = shop.tickets.get(200)

    assert (rec.requests[0].method, _path(rec.requests[0])) == ("GET", "/tickets/200")
    assert isinstance(ticket, models.TicketDetail)
    assert ticket.buyer_tg_user_id is None
    assert ticket.messages[0].author_tg_user_id is None
    assert ticket.messages[0].body == "Could you restore it?"
    assert ticket.omitted_messages == 0


def test_reply_ticket_request_result_and_idempotency_key():
    shop, rec, _ = make_shop()
    result = shop.tickets.reply(
        200,
        "Your downloads are restored.",
        idempotency_key="ticket-reply-00000001",
    )

    request = rec.requests[0]
    assert (request.method, _path(request)) == ("POST", "/tickets/200/replies")
    assert _body(request) == {"body": "Your downloads are restored."}
    assert request.headers["idempotency-key"] == "ticket-reply-00000001"
    assert isinstance(result, Completed)
    assert isinstance(result.result, models.TicketReplied)
    assert result.result.delivered is True


def test_close_ticket_request_and_result():
    shop, rec, _ = make_shop()
    result = shop.tickets.close(200)

    request = rec.requests[0]
    assert (request.method, _path(request)) == ("POST", "/tickets/200/close")
    assert _body(request) == {}
    assert "idempotency-key" in request.headers
    assert isinstance(result.result, models.TicketClosed)
    assert result.result.notified is True


def test_restore_downloads_request_result_and_idempotency_key():
    shop, rec, _ = make_shop()
    result = shop.orders.restore_downloads(
        100, downloads=5, idempotency_key="restore-order-0000001"
    )

    request = rec.requests[0]
    assert (request.method, _path(request)) == (
        "POST",
        "/orders/100/restore-downloads",
    )
    assert _body(request) == {"downloads": 5}
    assert request.headers["idempotency-key"] == "restore-order-0000001"
    assert isinstance(result.result, models.DownloadsRestored)
    assert (result.result.files, result.result.downloads) == (2, 5)


def test_restore_downloads_omits_default_download_count():
    shop, rec, _ = make_shop()
    shop.orders.restore_downloads(100)
    assert _body(rec.requests[0]) == {}


def test_reply_to_closed_ticket_is_refused():
    shop, _, _ = make_shop(lambda request: _error(400, "ticket_closed"))
    with pytest.raises(BadRequestError) as info:
        shop.tickets.reply(200, "A reply")
    assert info.value.code == "ticket_closed"
    assert info.value.idempotency_key


def test_reply_with_contact_details_needs_owner_approval():
    shop, _, _ = make_shop(
        lambda request: _error(
            403,
            "approval_required",
            reasons=["The reply contains an email address."],
        )
    )
    with pytest.raises(ApprovalRequiredError) as info:
        shop.tickets.reply(200, "Email support@example.com")
    assert info.value.reasons == ["The reply contains an email address."]
    assert info.value.idempotency_key


def test_overlong_reply_is_sent_whole_and_server_refusal_is_preserved():
    body = "x" * 4001

    def handler(request: httpx.Request) -> httpx.Response:
        assert _body(request) == {"body": body}
        return _error(422, "invalid_request", fields=["body"])

    shop, rec, _ = make_shop(handler)
    with pytest.raises(BadRequestError) as info:
        shop.tickets.reply(200, body)
    assert len(rec.requests) == 1
    assert info.value.fields == ["body"]


def test_restore_daily_cap_is_never_retried():
    shop, rec, sleeps = make_shop(
        lambda request: _error(429, "daily_cap"), max_retries=2
    )
    with pytest.raises(DailyCapError) as info:
        shop.orders.restore_downloads(100)
    assert info.value.code == "daily_cap"
    assert info.value.idempotency_key
    assert len(rec.requests) == 1
    assert sleeps == []


def test_reply_daily_cap_is_never_retried():
    shop, rec, sleeps = make_shop(
        lambda request: _error(429, "daily_cap"), max_retries=2
    )
    with pytest.raises(DailyCapError) as info:
        shop.tickets.reply(200, "A reply")
    assert info.value.code == "daily_cap"
    assert info.value.idempotency_key
    assert len(rec.requests) == 1
    assert sleeps == []


def test_async_support_and_fulfilment_parity():
    async def scenario():
        shop, rec, _ = make_async_shop()
        page = await shop.tickets.list(include_closed=False, limit=10)
        tickets = [ticket async for ticket in shop.tickets.iter(limit=5)]
        detail = await shop.tickets.get(200)
        replied = await shop.tickets.reply(200, "Restored.")
        closed = await shop.tickets.close(200)
        restored = await shop.orders.restore_downloads(100, downloads=4)
        await shop.close()
        return rec, page, tickets, detail, replied, closed, restored

    rec, page, tickets, detail, replied, closed, restored = asyncio.run(scenario())
    assert len(rec.requests) == 6
    assert isinstance(page, models.TicketPage)
    assert isinstance(tickets[0], models.TicketSummary)
    assert isinstance(detail, models.TicketDetail)
    assert isinstance(replied.result, models.TicketReplied)
    assert isinstance(closed.result, models.TicketClosed)
    assert isinstance(restored.result, models.DownloadsRestored)
    assert all(
        "idempotency-key" in request.headers
        for request in rec.requests
        if request.method == "POST"
    )


def test_async_refusals_are_typed_and_daily_cap_is_not_retried():
    responses = iter(
        [
            _error(400, "ticket_closed"),
            _error(403, "approval_required", reasons=["The reply contains a link."]),
            _error(429, "daily_cap"),
        ]
    )

    async def scenario():
        shop, rec, sleeps = make_async_shop(lambda request: next(responses))
        with pytest.raises(BadRequestError):
            await shop.tickets.reply(200, "First")
        with pytest.raises(ApprovalRequiredError):
            await shop.tickets.reply(200, "https://example.com")
        with pytest.raises(DailyCapError):
            await shop.orders.restore_downloads(100)
        await shop.close()
        return rec, sleeps

    rec, sleeps = asyncio.run(scenario())
    assert len(rec.requests) == 3
    assert sleeps == []
