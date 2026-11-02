"""Synchronous client: :class:`Shop`."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Set, Type, TypeVar, Union

import httpx
from pydantic import BaseModel

from . import _core
from .errors import ConnectionError, ShopError
from .models import (
    Attention,
    Category,
    CategoryAnswer,
    CategoryDeleteAnswer,
    CategoryDeleted,
    CategoryMoveAnswer,
    CategoryMoved,
    CategoryPage,
    ConfirmationInfo,
    ItemDetail,
    ItemPage,
    ItemSummary,
    OpenReceipt,
    OrderDetail,
    OrderPage,
    OrderSummary,
    PaymentPage,
    SalesSummary,
    StockLevel,
    StockPage,
    StoreResponse,
    WriteAnswer,
)
from .results import ConfirmationRequired, WriteResult

__all__ = ["Shop"]

M = TypeVar("M", bound=BaseModel)
A = TypeVar("A", bound=WriteAnswer)


class Shop:
    """Client for one store's merchant API.

    ``base_url`` is the API address the store's bot showed with the token, for
    example ``https://your-store.example/api/merchant/v1/``; it defaults to the
    ``SHOP_API_URL`` environment variable. ``token`` defaults to
    ``SHOP_API_TOKEN``. Calls are retried up to ``max_retries`` times on 429,
    502, 503, 504 and transport errors. Writes carry an ``Idempotency-Key``
    that stays the same across their retries, so a retry never changes the
    store twice; they are also retried on 409 ``idempotency_in_progress``,
    never on 429 ``daily_cap``, and not when Retry-After exceeds 30 seconds.
    Every error a write raises carries its ``idempotency_key``, to repeat the
    same call safely when the error does not say whether the change was made.

    Pass ``http_client`` to reuse your own :class:`httpx.Client` (its timeout
    and transport are kept, and :meth:`close` leaves it open).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url, token = _core.resolve_config(base_url, token)
        self._headers = _core.default_headers(token)
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more.")
        self._max_retries = max_retries
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise TypeError("http_client must be an httpx.Client (use AsyncShop for httpx.AsyncClient).")
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._sleep: Callable[[float], None] = time.sleep

        self.categories = Categories(self)
        self.items = Items(self)
        self.stock = Stock(self)
        self.orders = Orders(self)
        self.payments = Payments(self)
        self.reports = Reports(self)

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        return f"Shop(base_url={self._base_url!r})"

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP connection pool (unless it was passed in)."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Shop":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- top-level operations --------------------------------------------

    def store(self) -> StoreResponse:
        """The store and the token itself: who you are talking to, and what you may do."""
        return self._call("get_store", StoreResponse)

    def attention(self) -> Attention:
        """What needs the merchant now: payments for an operator, low stock, open tickets."""
        return self._call("needs_attention", Attention)

    # -- plumbing ---------------------------------------------------------

    def _call(
        self,
        operation_id: str,
        model: Type[M],
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
    ) -> M:
        response = self._send(operation_id, path_params=path_params, query=query)
        return _core.parse_response(response, model)

    def _write(
        self,
        operation_id: str,
        model: Type[A],
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[Any]:
        """One logical write: one ``Idempotency-Key``, reused by every retry.

        Every :class:`ShopError` it raises carries that key as
        ``idempotency_key``, so the caller can repeat the call safely.
        """
        key = _core.idempotency_key(idempotency_key)
        fixed_path: Dict[str, Any] = dict(path_params or {})
        fixed_body: Dict[str, Any] = dict(body or {})

        def confirm(confirmation: str, confirm_key: Optional[str]) -> WriteResult[Any]:
            if not _core.confirmable(operation_id):
                raise _core.not_confirmable()
            return self._write(
                operation_id,
                model,
                path_params=fixed_path,
                body={**fixed_body, "confirmation": confirmation},
                idempotency_key=confirm_key,
            )

        bind_preview = _core.preview_binder(model, confirm, preview_class=ConfirmationRequired)
        try:
            response = self._send(
                operation_id,
                path_params=fixed_path,
                body=fixed_body,
                idempotency_key=key,
                bind_preview=bind_preview,
            )
            return _core.parse_write(response, model, confirm, preview_class=ConfirmationRequired)
        except ShopError as exc:
            exc.idempotency_key = key
            raise

    def _send(
        self,
        operation_id: str,
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        bind_preview: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> httpx.Response:
        """Send one operation with retries; return the successful response or raise."""
        method, url, params, json_body = _core.build_request(
            self._base_url, operation_id, path_params, query, body
        )
        headers = _core.request_headers(self._headers, idempotency_key)
        write = json_body is not None
        attempt = 0
        answer_lost = False  # an earlier attempt may have reached the server
        while True:
            try:
                response = self._client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    answer_lost = answer_lost or _core.may_have_arrived(exc)
                    self._sleep(_core.backoff(attempt))
                    attempt += 1
                    continue
                raise ConnectionError(_core.connection_message(exc)) from exc
            if response.status_code < 400:
                return response
            delay = _core.retry_delay(response, attempt, write=write)
            if delay is not None and attempt < self._max_retries:
                answer_lost = answer_lost or _core.may_have_arrived(response)
                self._sleep(delay)
                attempt += 1
                continue
            raise _core.error_from_response(response, bind_preview, answer_lost=answer_lost)

    def _paginate(
        self, operation_id: str, page_model: Type[Any], query: Mapping[str, Any]
    ) -> Iterator[Any]:
        page = self._call(operation_id, page_model, query=query)
        seen: Set[str] = set()
        while True:
            yield from page.data
            cursor = page.next_cursor
            if not cursor or cursor in seen:
                return
            seen.add(cursor)
            # With a cursor the server reuses the first page's filters.
            page = self._call(operation_id, page_model, query={"cursor": cursor})


class _Resource:
    def __init__(self, shop: Shop) -> None:
        self._shop = shop


class Categories(_Resource):
    def list(self, *, limit: Optional[int] = None, cursor: Optional[str] = None) -> CategoryPage:
        """One page of categories, hidden ones included, oldest first."""
        return self._shop._call(
            "list_categories", CategoryPage, query={"limit": limit, "cursor": cursor}
        )

    def iter(self, *, limit: Optional[int] = None) -> Iterator[Category]:
        """Every category, following ``next_cursor``."""
        return self._shop._paginate("list_categories", CategoryPage, {"limit": limit})

    def create(
        self, title: str, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[Category]:
        """Create a category, shown in the store, at the end of the category list.

        Needs ``catalog:write``. Returns :class:`~apexai_shop.Completed` with
        the new :class:`Category`.
        """
        return self._shop._write(
            "create_category",
            CategoryAnswer,
            body={"title": title},
            idempotency_key=idempotency_key,
        )

    def update(
        self,
        category_id: int,
        *,
        title: Optional[str] = None,
        active: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[Category]:
        """Rename a category, or show (``active=True``) or hide it with every item in it.

        ``None`` leaves a field as it is; set at least one. Values that are
        already so answer ``noop``. Needs ``catalog:write``.
        """
        return self._shop._write(
            "update_category",
            CategoryAnswer,
            path_params={"category_id": category_id},
            body={"title": title, "active": active},
            idempotency_key=idempotency_key,
        )

    def move(
        self, category_id: int, to_index: int, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[CategoryMoved]:
        """Move a category to a new place in the list (0-based; past the end means last).

        Needs ``catalog:write``.
        """
        return self._shop._write(
            "move_category",
            CategoryMoveAnswer,
            path_params={"category_id": category_id},
            body={"to_index": to_index},
            idempotency_key=idempotency_key,
        )

    def delete(
        self,
        category_id: int,
        *,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[CategoryDeleted]:
        """Delete an empty category, in two steps.

        Without ``confirmation`` the answer is a
        :class:`~apexai_shop.ConfirmationRequired` preview: show the user its
        ``effect`` and call its ``confirm()`` once they agree (or pass
        ``confirmation=`` the preview's ``confirmation`` or its ``id``). Needs
        ``catalog:write``.
        """
        return self._shop._write(
            "delete_category",
            CategoryDeleteAnswer,
            path_params={"category_id": category_id},
            body={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )


class Items(_Resource):
    def list(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[int] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> ItemPage:
        """One page of items, hidden ones included.

        ``q`` matches words in the title or ``#id``; ``status`` is ``all``,
        ``on_sale`` or ``hidden``. With ``cursor`` the other filters are ignored.
        """
        return self._shop._call(
            "list_items",
            ItemPage,
            query={"q": q, "category": category, "status": status, "limit": limit, "cursor": cursor},
        )

    def iter(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[int] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[ItemSummary]:
        """Every matching item, following ``next_cursor``."""
        return self._shop._paginate(
            "list_items",
            ItemPage,
            {"q": q, "category": category, "status": status, "limit": limit},
        )

    def get(self, item_id: int) -> ItemDetail:
        """One item: text, price, state, delivery, stock, variants and bundle."""
        return self._shop._call("get_item", ItemDetail, path_params={"item_id": item_id})


class Stock(_Resource):
    def list(self, *, limit: Optional[int] = None, cursor: Optional[str] = None) -> StockPage:
        """One page of licence-key stock per key-selling item."""
        return self._shop._call("list_stock", StockPage, query={"limit": limit, "cursor": cursor})

    def iter(self, *, limit: Optional[int] = None) -> Iterator[StockLevel]:
        """Stock for every key-selling item, following ``next_cursor``."""
        return self._shop._paginate("list_stock", StockPage, {"limit": limit})


class Orders(_Resource):
    def list(
        self,
        *,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        buyer: Optional[int] = None,
        summary: Optional[bool] = None,
        cursor: Optional[str] = None,
    ) -> OrderPage:
        """One page of orders, newest first, in a window of at most 92 days.

        ``status`` is ``all``, ``paid``, ``refunded``, ``processing`` or
        ``pending``. ``buyer`` needs ``customers:read``. ``summary=True`` adds
        totals for every matching order. With ``cursor`` the other filters are
        ignored.
        """
        return self._shop._call(
            "list_orders",
            OrderPage,
            query={
                "status": status,
                "since": since,
                "until": until,
                "buyer": buyer,
                "summary": summary,
                "cursor": cursor,
            },
        )

    def iter(
        self,
        *,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        buyer: Optional[int] = None,
    ) -> Iterator[OrderSummary]:
        """Every matching order, newest first, following ``next_cursor``."""
        return self._shop._paginate(
            "list_orders",
            OrderPage,
            {"status": status, "since": since, "until": until, "buyer": buyer},
        )

    def get(self, order_id: int) -> OrderDetail:
        """One order, without licence keys or files, with charge ids masked."""
        return self._shop._call("get_order", OrderDetail, path_params={"order_id": order_id})


class Payments(_Resource):
    def list(self, *, cursor: Optional[str] = None) -> PaymentPage:
        """Payment health and one page of open Stars receipts, newest first."""
        return self._shop._call("list_payments", PaymentPage, query={"cursor": cursor})

    def iter(self) -> Iterator[OpenReceipt]:
        """Every open Stars receipt, following ``next_cursor``."""
        return self._shop._paginate("list_payments", PaymentPage, {})


class Reports(_Resource):
    def summary(self, days: int = 30) -> SalesSummary:
        """Sales for the last 7, 30 or 90 days and the same window before it."""
        return self._shop._call("sales_summary", SalesSummary, query={"days": days})
