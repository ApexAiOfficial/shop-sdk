"""Every operation of contract version 1: operation id -> method, path, query params and body fields.

Paths are relative to the API base URL (which ends in ``/api/merchant/v1``).
Reads are GETs with query parameters; writes send their body fields as JSON.
The table also records which writes carry an ``Idempotency-Key`` (staging key
text deliberately does not). Tests check it against
``contract/merchant-api-v1.json``.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Tuple


class Operation(NamedTuple):
    method: str
    path: str
    query: Tuple[str, ...] = ()
    body: Tuple[str, ...] = ()
    uses_idempotency_key: bool = False

    @property
    def is_write(self) -> bool:
        return self.method != "GET"


OPERATIONS: Dict[str, Operation] = {
    # --- reads -------------------------------------------------------------
    "get_approval": Operation("GET", "/approvals/{approval_id}"),
    "get_store": Operation("GET", "/store"),
    "needs_attention": Operation("GET", "/attention"),
    "list_categories": Operation("GET", "/categories", ("limit", "cursor")),
    "list_items": Operation("GET", "/items", ("q", "category", "status", "limit", "cursor")),
    "get_item": Operation("GET", "/items/{item_id}"),
    "list_stock": Operation("GET", "/stock", ("limit", "cursor")),
    "get_item_stock": Operation("GET", "/items/{item_id}/stock"),
    "list_orders": Operation(
        "GET",
        "/orders",
        ("status", "since", "until", "buyer", "summary", "cursor"),
    ),
    "get_order": Operation("GET", "/orders/{order_id}"),
    "list_tickets": Operation("GET", "/tickets", ("include_closed", "limit")),
    "get_ticket": Operation("GET", "/tickets/{ticket_id}"),
    "list_payments": Operation("GET", "/payments", ("cursor",)),
    "sales_summary": Operation("GET", "/reports/summary", ("days",)),
    "list_coupons": Operation("GET", "/coupons", ("status", "code", "limit", "cursor")),
    # --- writes ------------------------------------------------------------
    "create_category": Operation("POST", "/categories", body=("title",), uses_idempotency_key=True),
    "update_category": Operation(
        "PATCH", "/categories/{category_id}", body=("title", "active"), uses_idempotency_key=True
    ),
    "move_category": Operation(
        "POST", "/categories/{category_id}/move", body=("to_index",), uses_idempotency_key=True
    ),
    "delete_category": Operation(
        "POST",
        "/categories/{category_id}/delete",
        body=("confirmation",),
        uses_idempotency_key=True,
    ),
    "create_item": Operation(
        "POST",
        "/items",
        body=("category_id", "title", "description", "price_stars"),
        uses_idempotency_key=True,
    ),
    "update_item": Operation(
        "PATCH",
        "/items/{item_id}",
        body=("version", "title", "description", "price_stars", "active"),
        uses_idempotency_key=True,
    ),
    "move_item": Operation(
        "POST", "/items/{item_id}/move", body=("to_index",), uses_idempotency_key=True
    ),
    "delete_item": Operation(
        "POST", "/items/{item_id}/delete", body=("confirmation",), uses_idempotency_key=True
    ),
    "create_coupon": Operation(
        "POST",
        "/coupons",
        body=("code", "pct_off", "max_uses", "per_user_max_uses", "expires_at"),
        uses_idempotency_key=True,
    ),
    "update_coupon": Operation(
        "PATCH",
        "/coupons/{code}",
        body=("active", "max_uses", "per_user_max_uses", "expires_at", "confirmation"),
        uses_idempotency_key=True,
    ),
    "stage_key_import": Operation(
        "POST", "/items/{item_id}/keys/stage", body=("keys_text",)
    ),
    "commit_key_import": Operation(
        "POST",
        "/items/{item_id}/keys/commit",
        body=("enable_key_delivery", "staged_import"),
        uses_idempotency_key=True,
    ),
    "void_keys": Operation(
        "POST",
        "/items/{item_id}/keys/void",
        body=("confirmation",),
        uses_idempotency_key=True,
    ),
    "release_key_holds": Operation(
        "POST",
        "/items/{item_id}/keys/release",
        body=("confirmation",),
        uses_idempotency_key=True,
    ),
    "reply_ticket": Operation(
        "POST",
        "/tickets/{ticket_id}/replies",
        body=("body",),
        uses_idempotency_key=True,
    ),
    "close_ticket": Operation(
        "POST", "/tickets/{ticket_id}/close", uses_idempotency_key=True
    ),
    "restore_downloads": Operation(
        "POST",
        "/orders/{order_id}/restore-downloads",
        body=("downloads",),
        uses_idempotency_key=True,
    ),
}
