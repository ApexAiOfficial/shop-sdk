"""Every operation of contract version 1: operation id -> (method, path, query params).

Paths are relative to the API base URL (which ends in ``/api/merchant/v1``).
The test suite checks this table against ``contract/merchant-api-v1.json``.
"""

from __future__ import annotations

from typing import Dict, Tuple

Operation = Tuple[str, str, Tuple[str, ...]]

OPERATIONS: Dict[str, Operation] = {
    "get_store": ("GET", "/store", ()),
    "needs_attention": ("GET", "/attention", ()),
    "list_categories": ("GET", "/categories", ("limit", "cursor")),
    "list_items": ("GET", "/items", ("q", "category", "status", "limit", "cursor")),
    "get_item": ("GET", "/items/{item_id}", ()),
    "list_stock": ("GET", "/stock", ("limit", "cursor")),
    "list_orders": (
        "GET",
        "/orders",
        ("status", "since", "until", "buyer", "summary", "cursor"),
    ),
    "get_order": ("GET", "/orders/{order_id}", ()),
    "list_payments": ("GET", "/payments", ("cursor",)),
    "sales_summary": ("GET", "/reports/summary", ("days",)),
}
