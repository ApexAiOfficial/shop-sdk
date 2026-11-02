"""Asynchronous client: :class:`AsyncShop`, the same surface as :class:`Shop`."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Set, Type, TypeVar

import httpx
from pydantic import BaseModel

from . import _core
from .errors import ConnectionError
from .models import (
    Attention,
    Category,
    CategoryPage,
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
)

__all__ = ["AsyncShop"]

M = TypeVar("M", bound=BaseModel)


class AsyncShop:
    """Async client for one store's merchant API.

    Same arguments and methods as :class:`~apexai_shop.Shop`; methods are
    coroutines and ``iter`` methods return async iterators::

        async with AsyncShop() as shop:
            async for order in shop.orders.iter(status="paid"):
                ...
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url, token = _core.resolve_config(base_url, token)
        self._headers = _core.default_headers(token)
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more.")
        self._max_retries = max_retries
        if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
            raise TypeError("http_client must be an httpx.AsyncClient (use Shop for httpx.Client).")
        self._owns_client = http_client is None
        self._client = (
            http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)
        )
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

        self.categories = AsyncCategories(self)
        self.items = AsyncItems(self)
        self.stock = AsyncStock(self)
        self.orders = AsyncOrders(self)
        self.payments = AsyncPayments(self)
        self.reports = AsyncReports(self)

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        return f"AsyncShop(base_url={self._base_url!r})"

    # -- lifecycle --------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP connection pool (unless it was passed in)."""
        if self._owns_client:
            await self._client.aclose()

    aclose = close

    async def __aenter__(self) -> "AsyncShop":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    # -- top-level operations --------------------------------------------

    async def store(self) -> StoreResponse:
        """The store and the token itself: who you are talking to, and what you may do."""
        return await self._call("get_store", StoreResponse)

    async def attention(self) -> Attention:
        """What needs the merchant now: payments for an operator, low stock, open tickets."""
        return await self._call("needs_attention", Attention)

    # -- plumbing ---------------------------------------------------------

    async def _call(
        self,
        operation_id: str,
        model: Type[M],
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
    ) -> M:
        method, url, params = _core.build_request(
            self._base_url, operation_id, path_params, query
        )
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, params=params, headers=self._headers
                )
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    await self._sleep(_core.backoff(attempt))
                    attempt += 1
                    continue
                raise ConnectionError(_core.connection_message(exc)) from exc
            if response.status_code < 400:
                return _core.parse_response(response, model)
            delay = _core.retry_delay(response, attempt)
            if delay is not None and attempt < self._max_retries:
                await self._sleep(delay)
                attempt += 1
                continue
            raise _core.error_from_response(response)

    async def _paginate(
        self, operation_id: str, page_model: Type[Any], query: Mapping[str, Any]
    ) -> AsyncIterator[Any]:
        page = await self._call(operation_id, page_model, query=query)
        seen: Set[str] = set()
        while True:
            for row in page.data:
                yield row
            cursor = page.next_cursor
            if not cursor or cursor in seen:
                return
            seen.add(cursor)
            # With a cursor the server reuses the first page's filters.
            page = await self._call(operation_id, page_model, query={"cursor": cursor})


class _AsyncResource:
    def __init__(self, shop: AsyncShop) -> None:
        self._shop = shop


class AsyncCategories(_AsyncResource):
    async def list(
        self, *, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> CategoryPage:
        """One page of categories, hidden ones included, oldest first."""
        return await self._shop._call(
            "list_categories", CategoryPage, query={"limit": limit, "cursor": cursor}
        )

    def iter(self, *, limit: Optional[int] = None) -> AsyncIterator[Category]:
        """Every category, following ``next_cursor``."""
        return self._shop._paginate("list_categories", CategoryPage, {"limit": limit})


class AsyncItems(_AsyncResource):
    async def list(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[int] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> ItemPage:
        """One page of items, hidden ones included. With ``cursor`` the filters are ignored."""
        return await self._shop._call(
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
    ) -> AsyncIterator[ItemSummary]:
        """Every matching item, following ``next_cursor``."""
        return self._shop._paginate(
            "list_items",
            ItemPage,
            {"q": q, "category": category, "status": status, "limit": limit},
        )

    async def get(self, item_id: int) -> ItemDetail:
        """One item: text, price, state, delivery, stock, variants and bundle."""
        return await self._shop._call("get_item", ItemDetail, path_params={"item_id": item_id})


class AsyncStock(_AsyncResource):
    async def list(
        self, *, limit: Optional[int] = None, cursor: Optional[str] = None
    ) -> StockPage:
        """One page of licence-key stock per key-selling item."""
        return await self._shop._call(
            "list_stock", StockPage, query={"limit": limit, "cursor": cursor}
        )

    def iter(self, *, limit: Optional[int] = None) -> AsyncIterator[StockLevel]:
        """Stock for every key-selling item, following ``next_cursor``."""
        return self._shop._paginate("list_stock", StockPage, {"limit": limit})


class AsyncOrders(_AsyncResource):
    async def list(
        self,
        *,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        buyer: Optional[int] = None,
        summary: Optional[bool] = None,
        cursor: Optional[str] = None,
    ) -> OrderPage:
        """One page of orders, newest first, in a window of at most 92 days."""
        return await self._shop._call(
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
    ) -> AsyncIterator[OrderSummary]:
        """Every matching order, newest first, following ``next_cursor``."""
        return self._shop._paginate(
            "list_orders",
            OrderPage,
            {"status": status, "since": since, "until": until, "buyer": buyer},
        )

    async def get(self, order_id: int) -> OrderDetail:
        """One order, without licence keys or files, with charge ids masked."""
        return await self._shop._call(
            "get_order", OrderDetail, path_params={"order_id": order_id}
        )


class AsyncPayments(_AsyncResource):
    async def list(self, *, cursor: Optional[str] = None) -> PaymentPage:
        """Payment health and one page of open Stars receipts, newest first."""
        return await self._shop._call("list_payments", PaymentPage, query={"cursor": cursor})

    def iter(self) -> AsyncIterator[OpenReceipt]:
        """Every open Stars receipt, following ``next_cursor``."""
        return self._shop._paginate("list_payments", PaymentPage, {})


class AsyncReports(_AsyncResource):
    async def summary(self, days: int = 30) -> SalesSummary:
        """Sales for the last 7, 30 or 90 days and the same window before it."""
        return await self._shop._call("sales_summary", SalesSummary, query={"days": days})
