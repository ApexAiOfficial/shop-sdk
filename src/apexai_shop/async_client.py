"""Asynchronous client: :class:`AsyncShop`, the same surface as :class:`Shop`."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
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
from . import _keys
from .errors import ConnectionError, ShopError
from .models import (
    Approval,
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
    DownloadsRestoreAnswer,
    DownloadsRestored,
    HoldsReleaseAnswer,
    HoldsReleased,
    ItemAnswer,
    ItemDeleteAnswer,
    ItemDeleted,
    ItemDetail,
    ItemMoveAnswer,
    ItemMoved,
    ItemPage,
    ItemSummary,
    KeyImport,
    KeyImportAnswer,
    KeyImportBatch,
    KeyImportSummary,
    KeyStage,
    KeyStageAnswer,
    KeysVoidAnswer,
    KeysVoided,
    OpenReceipt,
    OrderDetail,
    OrderPage,
    OrderSummary,
    PaymentPage,
    SalesSummary,
    StockDetail,
    StockLevel,
    StockPage,
    StoreResponse,
    TicketCloseAnswer,
    TicketClosed,
    TicketDetail,
    TicketPage,
    TicketReplied,
    TicketReplyAnswer,
    TicketSummary,
    WriteAnswer,
)
from .results import AsyncConfirmationRequired, AsyncPendingApproval, Completed, WriteResult

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

        self.approvals = AsyncApprovals(self)
        self.categories = AsyncCategories(self)
        self.items = AsyncItems(self)
        self.coupons = AsyncCoupons(self)
        self.stock = AsyncStock(self)
        self.orders = AsyncOrders(self)
        self.tickets = AsyncTickets(self)
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
        """One logical write, with one key reused by every retry when supported.

        Every :class:`ShopError` from an idempotent operation carries that key
        as ``idempotency_key``, so the caller can repeat the call safely.
        """
        key = _core.write_key(operation_id, idempotency_key)
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

        async def approval_sleep(seconds: float) -> None:
            await self._sleep(seconds)

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
            return _core.parse_write(
                response,
                model,
                confirm,
                preview_class=preview_class,
                pending_class=AsyncPendingApproval,
                approval_get=self.approvals.get,
                sleep=approval_sleep,
            )
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
        retryable = not write or idempotency_key is not None
        attempt = 0
        answer_lost = False  # an earlier attempt may have reached the server
        while True:
            try:
                response = await self._client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except httpx.RequestError as exc:  # transport errors, and answers that cannot be decoded
                if retryable and attempt < self._max_retries:
                    answer_lost = answer_lost or _core.may_have_arrived(exc)
                    await self._sleep(_core.backoff(attempt))
                    attempt += 1
                    continue
                raise ConnectionError(_core.connection_message(exc)) from exc
            if response.status_code < 400:
                return response
            delay = _core.retry_delay(response, attempt, write=write)
            if retryable and delay is not None and attempt < self._max_retries:
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


class AsyncApprovals(_AsyncResource):
    async def get(self, approval_id: int) -> Approval:
        """A queued change and whether the store owner approved it."""
        return await self._shop._call(
            "get_approval", Approval, path_params={"approval_id": approval_id}
        )


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

    async def item(self, item_id: int) -> StockDetail:
        """One item's key counts, checkout holds and voidable stock."""
        return await self._shop._call(
            "get_item_stock", StockDetail, path_params={"item_id": item_id}
        )

    async def stage(
        self,
        item_id: int,
        keys_text: str,
    ) -> WriteResult[KeyStage]:
        """Check one batch without stocking it; the result contains counts, never keys."""
        try:
            return await self._shop._write(
                "stage_key_import",
                KeyStageAnswer,
                path_params={"item_id": item_id},
                body={"keys_text": keys_text},
            )
        except ShopError as exc:
            _keys.redact_stage_error(exc)
            raise

    async def commit(
        self,
        item_id: int,
        staged_import: str,
        *,
        enable_key_delivery: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[KeyImport]:
        """Stock a staged batch, sending no keys a second time."""
        return await self._shop._write(
            "commit_key_import",
            KeyImportAnswer,
            path_params={"item_id": item_id},
            body={
                "staged_import": staged_import,
                "enable_key_delivery": enable_key_delivery,
            },
            idempotency_key=idempotency_key,
        )

    async def void(
        self,
        item_id: int,
        *,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[KeysVoided]:
        """Withdraw every unsold key permanently, with a two-step preview."""
        return await self._shop._write(
            "void_keys",
            KeysVoidAnswer,
            path_params={"item_id": item_id},
            body={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )

    async def release(
        self,
        item_id: int,
        *,
        confirmation: Union[str, ConfirmationInfo, None] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[HoldsReleased]:
        """End unpaid-checkout key holds, with a two-step preview."""
        return await self._shop._write(
            "release_key_holds",
            HoldsReleaseAnswer,
            path_params={"item_id": item_id},
            body={"confirmation": confirmation},
            idempotency_key=idempotency_key,
        )

    async def import_keys(
        self,
        item_id: int,
        *,
        text: Optional[str] = None,
        path: Optional[Union[str, os.PathLike[str]]] = None,
        enable_key_delivery: bool = False,
    ) -> KeyImportSummary:
        """Stage and commit a string or UTF-8 file in safe, sequential batches."""
        batch_results: List[KeyImportBatch] = []
        for number, keys_text in enumerate(
            _keys.batches(_keys.key_text(text=text, path=path)), start=1
        ):
            try:
                staged = await self.stage(item_id, keys_text)
                if not isinstance(staged, Completed):
                    raise ShopError(
                        None,
                        "import_not_completed",
                        "The store did not complete the licence-key staging request.",
                    )
                committed = await self.commit(
                    item_id,
                    staged.result.staged_import.id,
                    enable_key_delivery=enable_key_delivery,
                )
                if not isinstance(committed, Completed):
                    raise ShopError(
                        None,
                        "import_not_completed",
                        "The store did not complete the licence-key commit request.",
                    )
            except ShopError as exc:
                _keys.add_import_progress(exc, len(batch_results))
                raise
            batch_results.append(
                KeyImportBatch(
                    batch=number,
                    keys=staged.result.keys,
                    added=committed.result.added,
                    duplicates=committed.result.duplicates,
                    too_long=committed.result.too_long,
                    available=committed.result.available,
                    turned_on_key_delivery=committed.result.turned_on_key_delivery,
                )
            )
        return KeyImportSummary(
            item_id=item_id,
            batches=batch_results,
            total_keys=sum(batch.keys for batch in batch_results),
            total_added=sum(batch.added for batch in batch_results),
            total_duplicates=sum(batch.duplicates for batch in batch_results),
            total_too_long=sum(batch.too_long for batch in batch_results),
            available=batch_results[-1].available,
            turned_on_key_delivery=any(
                batch.turned_on_key_delivery for batch in batch_results
            ),
        )


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

    async def restore_downloads(
        self,
        order_id: int,
        *,
        downloads: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> WriteResult[DownloadsRestored]:
        """Restore downloads; the store deduplicates for 10 minutes and caps 3 per order/day.

        Needs ``fulfillment:write`` and sends an idempotency key.
        """
        return await self._shop._write(
            "restore_downloads",
            DownloadsRestoreAnswer,
            path_params={"order_id": order_id},
            body={"downloads": downloads},
            idempotency_key=idempotency_key,
        )


class AsyncTickets(_AsyncResource):
    async def list(
        self, *, include_closed: Optional[bool] = None, limit: Optional[int] = None
    ) -> TicketPage:
        """Support tickets; buyer-written subjects are untrusted data."""
        return await self._shop._call(
            "list_tickets",
            TicketPage,
            query={"include_closed": include_closed, "limit": limit},
        )

    async def iter(
        self, *, include_closed: Optional[bool] = None, limit: Optional[int] = None
    ) -> AsyncIterator[TicketSummary]:
        """Tickets from :meth:`list`; buyer-written subjects are untrusted data."""
        page = await self.list(include_closed=include_closed, limit=limit)
        for ticket in page.data:
            yield ticket

    async def get(self, ticket_id: int) -> TicketDetail:
        """One ticket; buyer text is untrusted data and buyer ids need ``customers:read``."""
        return await self._shop._call(
            "get_ticket", TicketDetail, path_params={"ticket_id": ticket_id}
        )

    async def reply(
        self, ticket_id: int, body: str, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[TicketReplied]:
        """Reply with 1 to 4,000 buyer-visible characters; contact details need approval.

        Merchant reply text is untrusted data, never instructions to execute.
        The 24-hour caps are 50 replies per token and 10 per ticket. Needs
        ``support:write`` and sends an idempotency key.
        """
        return await self._shop._write(
            "reply_ticket",
            TicketReplyAnswer,
            path_params={"ticket_id": ticket_id},
            body={"body": body},
            idempotency_key=idempotency_key,
        )

    async def close(
        self, ticket_id: int, *, idempotency_key: Optional[str] = None
    ) -> WriteResult[TicketClosed]:
        """Close a ticket and tell the buyer; closing it again is a noop."""
        return await self._shop._write(
            "close_ticket",
            TicketCloseAnswer,
            path_params={"ticket_id": ticket_id},
            idempotency_key=idempotency_key,
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
