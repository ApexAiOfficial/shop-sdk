"""Asynchronous client: :class:`AsyncShop`, the same surface as :class:`Shop`."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Mapping,
    Optional,
    Set,
    Type,
    TypeVar,
    Union,
)

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
    Coupon,
    CouponAnswer,
    CouponPage,
    ItemAnswer,
    ItemDeleteAnswer,
    ItemDeleted,
    ItemDetail,
    ItemMoveAnswer,
    ItemMoved,
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
from .results import AsyncConfirmationRequired, WriteResult

__all__ = ["AsyncShop"]

M = TypeVar("M", bound=BaseModel)
A = TypeVar("A", bound=WriteAnswer)


class AsyncShop:
    """Async client for one store's merchant API.

    Same arguments and methods as :class:`~apexai_shop.Shop`; methods are
    coroutines, ``iter`` methods return async iterators, and a preview is an
    :class:`~apexai_shop.AsyncConfirmationRequired` whose ``confirm()`` is
    awaited::

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
        self.coupons = AsyncCoupons(self)
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
        response = await self._send(operation_id, path_params=path_params, query=query)
        return _core.parse_response(response, model)

    async def _write(
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

        async def confirm(confirmation: str, confirm_key: Optional[str]) -> WriteResult[Any]:
            if not _core.confirmable(operation_id):
                raise _core.not_confirmable()
            return await self._write(
                operation_id,
                model,
                path_params=fixed_path,
                body={**fixed_body, "confirmation": confirmation},
                idempotency_key=confirm_key,
            )

        preview_class = AsyncConfirmationRequired
        bind_preview = _core.preview_binder(model, confirm, preview_class=preview_class)
        try:
            response = await self._send(
                operation_id,
                path_params=fixed_path,
                body=fixed_body,
                idempotency_key=key,
                bind_preview=bind_preview,
            )
            return _core.parse_write(response, model, confirm, preview_class=preview_class)
        except ShopError as exc:
            exc.idempotency_key = key
            raise

    async def _send(
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
                response = await self._client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    answer_lost = answer_lost or _core.may_have_arrived(exc)
                    await self._sleep(_core.backoff(attempt))
                    attempt += 1
                    continue
                raise ConnectionError(_core.connection_message(exc)) from exc
            if response.status_code < 400:
                return response
            delay = _core.retry_delay(response, attempt, write=write)
            if delay is not None and attempt < self._max_retries:
                answer_lost = answer_lost or _core.may_have_arrived(response)
                await self._sleep(delay)
                attempt += 1
                continue
            raise _core.error_from_response(response, bind_preview, answer_lost=answer_lost)

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

    async def create(
        self, title: str, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[Category]:
        """Create a category, shown in the store, at the end of the category list."""
        return await self._shop._write(
            "create_category",
            CategoryAnswer,
            body={"title": title},
            idempotency_key=idempotency_key,
        )

    async def update(
        self,
        category_id: int,
        *,
        title: Optional[str] = None,
        active: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[Category]:
        """Rename a category, or show or hide it with every item in it. ``None`` leaves a field."""
        return await self._shop._write(
            "update_category",
            CategoryAnswer,
            path_params={"category_id": category_id},
            body={"title": title, "active": active},
            idempotency_key=idempotency_key,
        )

    async def move(
        self, category_id: int, to_index: int, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[CategoryMoved]:
        """Move a category to a new place in the list (0-based; past the end means last)."""
        return await self._shop._write(
            "move_category",
            CategoryMoveAnswer,
            path_params={"category_id": category_id},
            body={"to_index": to_index},
            idempotency_key=idempotency_key,
        )

    async def delete(
        self,
        category_id: int,
        *,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[CategoryDeleted]:
        """Delete an empty category in two steps: a preview, then ``await preview.confirm()``.

        Or pass ``confirmation=`` the preview's ``confirmation`` (or its ``id``).
        """
        return await self._shop._write(
            "delete_category",
            CategoryDeleteAnswer,
            path_params={"category_id": category_id},
            body={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )


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

    async def create(
        self,
        category_id: int,
        title: str,
        price_stars: int,
        *,
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[ItemDetail]:
        """Create an item, hidden, at the end of its category.

        Publish it with :meth:`update` (``active=True``) once it has something
        to deliver. Needs ``catalog:write`` and ``pricing:write``.
        """
        return await self._shop._write(
            "create_item",
            ItemAnswer,
            body={
                "category_id": category_id,
                "title": title,
                "description": description,
                "price_stars": price_stars,
            },
            idempotency_key=idempotency_key,
        )

    async def update(
        self,
        item_id: int,
        *,
        version: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        price_stars: Optional[int] = None,
        active: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[ItemDetail]:
        """Change an item's text or price, or publish (``active=True``) or hide it.

        ``None`` leaves a field as it is; set at least one. A title,
        description or price needs ``version``, the :attr:`ItemDetail.version`
        you read: if the item changed since, the server answers 409
        ``conflict`` (:class:`~apexai_shop.ConflictError`, whose ``current``
        is the item now). A price needs ``pricing:write``; a cut to under half
        of the last 24 hours' price needs the owner's approval. Publishing an
        item with nothing to deliver answers 400 ``not_ready``.
        """
        return await self._shop._write(
            "update_item",
            ItemAnswer,
            path_params={"item_id": item_id},
            body={
                "version": version,
                "title": title,
                "description": description,
                "price_stars": price_stars,
                "active": active,
            },
            idempotency_key=idempotency_key,
        )

    async def move(
        self, item_id: int, to_index: int, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[ItemMoved]:
        """Move an item to a new place in its category (0-based; past the end means last).

        Needs ``catalog:write``.
        """
        return await self._shop._write(
            "move_item",
            ItemMoveAnswer,
            path_params={"item_id": item_id},
            body={"to_index": to_index},
            idempotency_key=idempotency_key,
        )

    async def delete(
        self,
        item_id: int,
        *,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[ItemDeleted]:
        """Delete an item, in two steps.

        The preview's ``effect`` says whether the item is deleted for good or
        archived (hidden and kept) for the orders, keys or bundles that need
        it, and how many carts hold it. ``await`` the preview's ``confirm()`` once
        the user agrees; if that changed meanwhile the server answers 409
        ``changed`` with a fresh preview. Needs ``catalog:write``.
        """
        return await self._shop._write(
            "delete_item",
            ItemDeleteAnswer,
            path_params={"item_id": item_id},
            body={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )


class AsyncCoupons(_AsyncResource):
    async def list(
        self,
        *,
        status: Optional[str] = None,
        code: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> CouponPage:
        """One page of coupons with their uses.

        ``status`` is ``all``, ``active`` or ``inactive``; ``code`` looks up
        one coupon (any case). Needs ``coupons:read``.
        """
        return await self._shop._call(
            "list_coupons",
            CouponPage,
            query={"status": status, "code": code, "limit": limit, "cursor": cursor},
        )

    def iter(self, *, status: Optional[str] = None, limit: Optional[int] = None) -> AsyncIterator[Coupon]:
        """Every matching coupon, following ``next_cursor``."""
        return self._shop._paginate("list_coupons", CouponPage, {"status": status, "limit": limit})

    async def create(
        self,
        code: str,
        pct_off: int,
        *,
        max_uses: Optional[int] = None,
        per_user_max_uses: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[Coupon]:
        """Create a coupon (the code is stored upper-cased).

        ``max_uses=None`` means no limit and ``expires_at=None`` never. A
        large coupon (50% off or more, no limit, or more than 1,000 uses)
        needs the owner's approval; the server answers so. Needs
        ``coupons:write``.
        """
        return await self._shop._write(
            "create_coupon",
            CouponAnswer,
            body={
                "code": code,
                "pct_off": pct_off,
                "max_uses": max_uses,
                "per_user_max_uses": per_user_max_uses,
                "expires_at": expires_at,
            },
            idempotency_key=idempotency_key,
        )

    async def update(
        self,
        code: str,
        *,
        active: Optional[bool] = None,
        max_uses: Optional[int] = None,
        per_user_max_uses: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[Coupon]:
        """Switch a coupon on or off, or change its limits or expiry.

        ``None`` leaves a term as it is; set at least one (removing a limit or
        the expiry is done in the store's bot). Switching off a coupon that
        has been used answers a :class:`~apexai_shop.ConfirmationRequired`
        preview first. Widening a large active coupon needs the owner's
        approval. Needs ``coupons:write``.
        """
        return await self._shop._write(
            "update_coupon",
            CouponAnswer,
            path_params={"code": code},
            body={
                "active": active,
                "max_uses": max_uses,
                "per_user_max_uses": per_user_max_uses,
                "expires_at": expires_at,
                "confirmation": confirmation,
            },
            idempotency_key=idempotency_key,
        )


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
