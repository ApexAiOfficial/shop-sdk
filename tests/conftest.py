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

CATEGORY = {"id": 1, "title": "Guides", "is_active": True, "items": 4, "hidden_items": 1}

COUPON = {
    "code": "SPRING10",
    "pct_off": 10,
    "is_active": True,
    "max_uses": 100,
    "per_user_max_uses": 1,
    "expires_at": "2026-12-01T00:00:00Z",
    "created_at": "2026-11-01T09:00:00Z",
    "uses": {"paid": 3, "open_checkouts": 1},
}


def answer(status: str = "done", **fields: Any) -> Dict[str, Any]:
    """A write's envelope: ``done`` unless ``status`` says otherwise, every field present."""
    body: Dict[str, Any] = {
        "status": status,
        "replayed": False,
        "noop": False,
        "result": None,
        "confirmation": None,
        "approval": None,
        "effect": None,
        "next": None,
    }
    body.update(fields)
    return body


def preview(confirmation_id: str = "conf_abc123", summary: str = "Delete the category Guides.") -> Dict[str, Any]:
    """A ``confirmation_required`` envelope, as a two-step write's first call answers."""
    return answer(
        "confirmation_required",
        confirmation={"id": confirmation_id, "expires_at": "2026-11-02T12:05:00Z"},
        effect={"summary": summary, "details": {"category_id": 1, "title": "Guides"}},
        next="Show the user this effect; call delete_category again with this confirmation only after they agree.",
    )


SAMPLES: Dict[str, Any] = {
    "get_store": {
        "api_version": "1", "api_revision": "2026-11-02",
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
    "get_item_stock": {
        "item_id": 10,
        "title": "Guide",
        "sells_keys": True,
        "levels": {"available": 5, "reserved": 0, "sold": 2, "revoked": 0, "void": 0},
        "voidable": 5,
        "holds": {"live": 0, "lapsed": 0},
        "low_stock_threshold": 3,
        "low": False,
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
    "list_tickets": {
        "untrusted_fields": ["subject"],
        "data": [
            {
                "id": 200,
                "status": "open",
                "subject": "My download expired",
                "created_at": "2026-11-01T10:00:00Z",
                "last_message_at": "2026-11-01T10:05:00Z",
            }
        ],
    },
    "get_ticket": {
        "untrusted_fields": ["subject", "messages[].body"],
        "id": 200,
        "status": "open",
        "subject": "My download expired",
        "created_at": "2026-11-01T10:00:00Z",
        "buyer_tg_user_id": None,
        "messages": [
            {
                "author": "buyer",
                "author_tg_user_id": None,
                "body": "Could you restore it?",
                "created_at": "2026-11-01T10:05:00Z",
            }
        ],
        "omitted_messages": 0,
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
    "list_coupons": {"data": [COUPON], "next_cursor": None},
    "create_category": answer(result=CATEGORY),
    "update_category": answer(result=dict(CATEGORY, title="Handbooks")),
    "move_category": answer(result={"id": 1, "from_index": 2, "to_index": 0}),
    "delete_category": answer(
        result={"deleted": True, "category_id": 1},
        effect={"summary": "Delete the category Guides.", "details": {"category_id": 1}},
    ),
    "move_item": answer(result={"id": 10, "from_index": 3, "to_index": 0}),
    "delete_item": answer(
        result={"item_id": 10, "outcome": "archived", "bundle_ids": []},
        effect={"summary": "Archive the item Guide.", "details": {"outcome": "archived", "carts": 0}},
    ),
    "create_coupon": answer(result=COUPON),
    "update_coupon": answer(result=dict(COUPON, is_active=False)),
    "stage_key_import": answer(
        result={
            "item_id": 10,
            "staged_import": {
                "id": "stage_abc123",
                "expires_at": "2026-11-02T12:15:00Z",
            },
            "keys": 2,
            "would_add": 2,
            "duplicates": 0,
            "too_long": 0,
            "available_now": 5,
            "available_after": 7,
            "turns_on_key_delivery": False,
            "members_only": False,
            "comma_lines": 0,
            "hidden": False,
        }
    ),
    "commit_key_import": answer(
        result={
            "item_id": 10,
            "added": 2,
            "duplicates": 0,
            "too_long": 0,
            "available": 7,
            "members_only": False,
            "turned_on_key_delivery": False,
        }
    ),
    "void_keys": answer(result={"item_id": 10, "voided": 5}),
    "release_key_holds": answer(
        result={"item_id": 10, "released": 2, "unheld": 7, "until": "2026-11-02T12:05:00Z"}
    ),
    "reply_ticket": answer(
        result={"ticket_id": 200, "status": "answered", "delivered": True}
    ),
    "close_ticket": answer(
        result={"ticket_id": 200, "status": "closed", "notified": True}
    ),
    "restore_downloads": answer(
        result={
            "order_id": 100,
            "files": 2,
            "downloads": 5,
            "expires_at": "2026-11-09T12:00:00Z",
            "gift_unredeemed": False,
            "notified": True,
        }
    ),
}
SAMPLES["create_item"] = answer(result=dict(SAMPLES["get_item"], status="hidden"))
SAMPLES["update_item"] = answer(result=dict(SAMPLES["get_item"], price_stars=250))

#: (method, request path after the base URL) -> operation id, for routing mock requests.
ROUTES = {
    ("GET", "/store"): "get_store",
    ("GET", "/attention"): "needs_attention",
    ("GET", "/categories"): "list_categories",
    ("GET", "/items"): "list_items",
    ("GET", "/items/10"): "get_item",
    ("GET", "/stock"): "list_stock",
    ("GET", "/items/10/stock"): "get_item_stock",
    ("GET", "/orders"): "list_orders",
    ("GET", "/orders/100"): "get_order",
    ("POST", "/orders/100/restore-downloads"): "restore_downloads",
    ("GET", "/tickets"): "list_tickets",
    ("GET", "/tickets/200"): "get_ticket",
    ("POST", "/tickets/200/replies"): "reply_ticket",
    ("POST", "/tickets/200/close"): "close_ticket",
    ("GET", "/payments"): "list_payments",
    ("GET", "/reports/summary"): "sales_summary",
    ("POST", "/categories"): "create_category",
    ("PATCH", "/categories/1"): "update_category",
    ("POST", "/categories/1/move"): "move_category",
    ("POST", "/categories/1/delete"): "delete_category",
    ("GET", "/coupons"): "list_coupons",
    ("POST", "/items"): "create_item",
    ("PATCH", "/items/10"): "update_item",
    ("POST", "/items/10/move"): "move_item",
    ("POST", "/items/10/delete"): "delete_item",
    ("POST", "/coupons"): "create_coupon",
    ("PATCH", "/coupons/SPRING10"): "update_coupon",
    ("POST", "/items/10/keys/stage"): "stage_key_import",
    ("POST", "/items/10/keys/commit"): "commit_key_import",
    ("POST", "/items/10/keys/void"): "void_keys",
    ("POST", "/items/10/keys/release"): "release_key_holds",
}

PREFIX = "/api/merchant/v1"


def route(request: httpx.Request) -> httpx.Response:
    """A handler answering every operation with its sample payload."""
    path = request.url.path
    assert path.startswith(PREFIX + "/"), path
    op = ROUTES.get((request.method, path[len(PREFIX):]))
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
