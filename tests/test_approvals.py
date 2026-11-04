"""Owner approval reads and the bound sync/async wait helpers."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx
import pytest

from apexai_shop import (
    ApprovalTimeoutError,
    AsyncPendingApproval,
    PendingApproval,
    models,
)
from conftest import APPROVAL, PREFIX, answer, make_async_shop, make_shop


def _approval(status: str, **fields: Any) -> Dict[str, Any]:
    payload = dict(APPROVAL, status=status)
    if status != "pending":
        payload["decided_at"] = "2026-11-02T12:01:00Z"
    payload.update(fields)
    return payload


def _pending_write() -> Dict[str, Any]:
    return answer(
        "pending_approval",
        approval={
            "id": 12,
            "status": "pending",
            "expires_at": "2026-11-02T12:15:00Z",
            "reasons": ["price_cut_over_50_percent"],
        },
        effect=APPROVAL["effect"],
        next="Wait for the store owner to tap Approve in Telegram.",
    )


def test_get_approval_request_and_model() -> None:
    shop, rec, _ = make_shop()
    approval = shop.approvals.get(12)
    assert isinstance(approval, models.Approval)
    assert approval.status == "pending"
    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"{PREFIX}/approvals/12"
    assert request.content == b""
    assert "idempotency-key" not in request.headers


@pytest.mark.parametrize("status", ["done", "rejected", "expired", "changed", "failed"])
def test_pending_wait_returns_every_final_status(status: str) -> None:
    approvals: List[Dict[str, Any]] = [_approval("pending"), _approval(status)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(202, json=_pending_write())
        return httpx.Response(200, json=approvals.pop(0))

    shop, rec, sleeps = make_shop(handler)
    pending = shop.items.update(10, version="v1", price_stars=100)
    assert isinstance(pending, PendingApproval)
    final = pending.wait(timeout=10, poll=2)
    assert final.status == status
    assert sleeps == [2]
    assert [request.url.path for request in rec.requests[1:]] == [
        f"{PREFIX}/approvals/12",
        f"{PREFIX}/approvals/12",
    ]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/items/10"),
        ("POST", "/coupons"),
        ("PATCH", "/coupons/SPRING10"),
    ],
)
def test_documented_202_answers_parse_as_pending(method: str, path: str) -> None:
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending_write()))
    if (method, path) == ("PATCH", "/items/10"):
        result = shop.items.update(10, version="v1", price_stars=100)
    elif method == "POST":
        result = shop.coupons.create("BIG", 50)
    else:
        result = shop.coupons.update("SPRING10", max_uses=2000)
    assert isinstance(result, PendingApproval)
    assert result.approval.reasons == ["price_cut_over_50_percent"]


def test_pending_wait_times_out_without_real_sleep() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(202, json=_pending_write())
        return httpx.Response(200, json=_approval("pending"))

    shop, rec, sleeps = make_shop(handler)
    pending = shop.items.update(10, version="v1", price_stars=100)
    with pytest.raises(ApprovalTimeoutError) as info:
        pending.wait(timeout=5, poll=2)
    assert info.value.approval_id == 12
    assert info.value.timeout == 5
    assert sleeps == [2, 2, 1]
    assert len(rec.requests) == 4  # one write and three approval reads


def test_pending_wait_uses_read_retry_after_before_polling_again() -> None:
    queue = [
        httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "3"}),
        httpx.Response(200, json=_approval("pending")),
        httpx.Response(200, json=_approval("done", result={"id": 10})),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(202, json=_pending_write())
        return queue.pop(0)

    shop, _, sleeps = make_shop(handler)
    pending = shop.items.update(10, version="v1", price_stars=100)
    assert pending.wait(timeout=10, poll=2).status == "done"
    assert sleeps == [3, 2]


def test_async_pending_wait_parity() -> None:
    approvals = [_approval("pending"), _approval("rejected")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(202, json=_pending_write())
        return httpx.Response(200, json=approvals.pop(0))

    async def main() -> tuple[models.Approval, List[float]]:
        shop, _, sleeps = make_async_shop(handler)
        pending = await shop.items.update(10, version="v1", price_stars=100)
        assert isinstance(pending, AsyncPendingApproval)
        return await pending.wait(timeout=10, poll=2), sleeps

    final, sleeps = asyncio.run(main())
    assert final.status == "rejected"
    assert sleeps == [2]


@pytest.mark.parametrize(("timeout", "poll"), [(-1, 2), (1, 0), (1, float("inf"))])
def test_wait_rejects_invalid_timing(timeout: float, poll: float) -> None:
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending_write()))
    pending = shop.items.update(10, version="v1", price_stars=100)
    with pytest.raises(ValueError):
        pending.wait(timeout=timeout, poll=poll)
