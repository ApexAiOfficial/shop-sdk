"""Every operation of contract version 1: operation id -> method, path, query params and body fields.

Paths are relative to the API base URL (which ends in ``/api/merchant/v1``).
Reads are GETs with query parameters; writes send their body fields as JSON
and carry an ``Idempotency-Key`` header. The test suite checks this table
against ``contract/merchant-api-v1.json``.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Tuple


class Operation(NamedTuple):
    method: str
    path: str
    query: Tuple[str, ...] = ()
    body: Tuple[str, ...] = ()

    @property
    def is_write(self) -> bool:
        return self.method != "GET"


OPERATIONS: Dict[str, Operation] = {
    # --- reads -------------------------------------------------------------
    "get_store": Operation("GET", "/store"),
    "needs_attention": Operation("GET", "/attention"),
    "list_categories": Operation("GET", "/categories", ("limit", "cursor")),
    "list_items": Operation("GET", "/items", ("q", "category", "status", "limit", "cursor")),
    "get_item": Operation("GET", "/items/{item_id}"),
    "list_stock": Operation("GET", "/stock", ("limit", "cursor")),
    "list_orders": Operation(
        "GET",
        "/orders",
        ("status", "since", "until", "buyer", "summary", "cursor"),
    ),
    "get_order": Operation("GET", "/orders/{order_id}"),
    "list_payments": Operation("GET", "/payments", ("cursor",)),
    "sales_summary": Operation("GET", "/reports/summary", ("days",)),
    # --- writes ------------------------------------------------------------
    "create_category": Operation("POST", "/categories", body=("title",)),
    "update_category": Operation("PATCH", "/categories/{category_id}", body=("title", "active")),
    "move_category": Operation("POST", "/categories/{category_id}/move", body=("to_index",)),
    "delete_category": Operation(
        "POST", "/categories/{category_id}/delete", body=("confirmation",)
    ),
}
