"""Shared fixtures: sample payloads and clients wired to httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

import httpx
import pytest

from apexai_shop import AsyncShop, Shop

BASE_URL = "https://your-store.example/api/merchant/v1/"
TOKEN = "shp_test_SECRET_token_value_123"
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contract" / "merchant-api-v1.json"


@pytest.fixture(scope="session")
def contract() -> Dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


SALES = {
    "paid_orders": 3,
    "gross_stars": 900,
    "refunded_orders": 1,
    "refunded_stars": 100,
    "net_stars": 800,
    "average_order_stars": 300.0,
    "coupon_discounts": 0,
    "referral_discounts": 0,
    "top_items": [{"title": "Guide", "quantity": 2, "revenue_stars": 600}],
    "membership_charges": 0,
    "membership_stars": 0,
    "live_members": 0,
}

SAMPLES: Dict[str, Any] = {
    "get_store": {
        "api_version": "1",
        "store": {"name": "Demo Store", "tier": "business", "currency": "XTR"},
        "token": {
            "id": 7,
            "name": "reports",
            "scopes": ["catalog:read", "orders:read"],
            "expires_at": "2027-01-01T00:00:00Z",
        },
    },
    "needs_attention": {"needs_operator": 1, "low_stock": 2, "open_tickets": 0},
    "list_categories": {
        "data": [{"id": 1, "title": "Guides", "is_active": True, "items": 4, "hidden_items": 1}],
        "next_cursor": None,
    },
    "list_items": {
        "data": [
            {
                "id": 10,
                "category_id": 1,
                "title": "Guide",
                "price_stars": 300,
                "status": "on_sale",
                "is_bundle": False,
                "sells_keys": True,
                "has_photo": False,
            }
        ],
        "next_cursor": None,
    },
    "get_item": {
        "id": 10,
        "category_id": 1,
        "category_title": "Guides",
        "title": "Guide",
        "description": "A guide.",
        "price_stars": 300,
        "status": "on_sale",
        "has_sales": True,
        "has_photo": False,
        "delivery": "keys",
        "file": None,
        "stock": {"available": 5, "reserved": 0, "sold": 2, "revoked": 0, "void": 0},
        "variants": [],
        "bundle": None,
        "members_only": False,
        "publish_blocker": None,
        "version": "v1",
    },
    "list_stock": {
        "data": [
            {
                "item_id": 10,
                "title": "Guide",
                "available": 5,
                "reserved": 0,
                "sold": 2,
                "revoked": 0,
                "void": 0,
            }
        ],
        "next_cursor": None,
    },
    "list_orders": {
        "data": [
            {
                "id": 100,
                "buyer_tg_user_id": None,
                "total_stars": 300,
                "status": "paid",
                "created_at": "2026-10-30T12:00:00Z",
                "paid_at": "2026-10-30T12:01:00Z",
            }
        ],
        "since": "2026-08-01T00:00:00Z",
        "until": "2026-11-01T00:00:00Z",
        "summary": None,
        "next_cursor": None,
    },
    "get_order": {
        "id": 100,
        "buyer_tg_user_id": None,
        "status": "paid",
        "created_at": "2026-10-30T12:00:00Z",
        "paid_at": "2026-10-30T12:01:00Z",
        "subtotal_stars": 300,
        "coupon_discount_stars": 0,
        "referral_discount_stars": 0,
        "total_stars": 300,
        "refunded_at": None,
        "refund_reason": None,
        "items": [{"title": "Guide", "quantity": 1, "unit_price_stars": 300}],
        "receipts": [{"state": "paid", "charge": "abc123", "amount_stars": 300}],
        "grants": [],
        "key_counts": {"delivered": 1, "revoked": 0},
        "refundable": True,
        "refund_request": None,
    },
    "list_payments": {
        "health": {
            "pending_fulfillment": 0,
            "unverified": 0,
            "needs_operator": 1,
            "oldest_open_age_seconds": 60,
            "reconciler_last_success_age_seconds": 30,
            "reconciler_stale": False,
        },
        "data": [
            {
                "id": 5,
                "state": "needs_operator",
                "anomaly_code": "amount_mismatch",
                "order_id": 100,
                "membership_id": None,
                "total_amount": 300,
                "received_at": "2026-10-30T12:01:00Z",
            }
        ],
        "next_cursor": None,
    },
    "sales_summary": {
        "days": 30,
        "current": SALES,
        "previous": SALES,
        "series": [100, 200, 500],
        "bucket_days": 10,
        "new_buyers": 2,
        "returning_buyers": 1,
        "checkouts": 5,
        "checkouts_paid": 3,
        "open_tickets": 0,
    },
}

#: Request path (after the base URL) -> operation id, for routing mock requests.
ROUTES = {
    "/store": "get_store",
    "/attention": "needs_attention",
    "/categories": "list_categories",
    "/items": "list_items",
    "/items/10": "get_item",
    "/stock": "list_stock",
    "/orders": "list_orders",
    "/orders/100": "get_order",
    "/payments": "list_payments",
    "/reports/summary": "sales_summary",
}

PREFIX = "/api/merchant/v1"


def route(request: httpx.Request) -> httpx.Response:
    """A handler answering every operation with its sample payload."""
    path = request.url.path
    assert path.startswith(PREFIX + "/"), path
    op = ROUTES.get(path[len(PREFIX):])
    if op is None:
        return httpx.Response(404, json={"error": "not_found"})
    return httpx.Response(200, json=SAMPLES[op])


class Recorder:
    """Wraps a handler and keeps every request it saw."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] = route) -> None:
        self.handler = handler
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def make_shop(handler: Callable[[httpx.Request], httpx.Response] = route, **kwargs: Any):
    recorder = Recorder(handler)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    shop = Shop(kwargs.pop("base_url", BASE_URL), kwargs.pop("token", TOKEN), http_client=client, **kwargs)
    sleeps: List[float] = []
    shop._sleep = sleeps.append
    return shop, recorder, sleeps


def make_async_shop(handler: Callable[[httpx.Request], httpx.Response] = route, **kwargs: Any):
    recorder = Recorder(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    shop = AsyncShop(
        kwargs.pop("base_url", BASE_URL), kwargs.pop("token", TOKEN), http_client=client, **kwargs
    )
    sleeps: List[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    shop._sleep = fake_sleep
    return shop, recorder, sleeps
