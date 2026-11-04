"""AsyncShop: same surface and behaviour as Shop. Uses asyncio.run, no plugins."""

from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest

from apexai_shop import AsyncShop, BadRequestError, ConnectionError, RateLimitError, Shop, models
from conftest import BASE_URL, PREFIX, SAMPLES, TOKEN, make_async_shop, make_shop

RESOURCES = ["approvals", "categories", "items", "stock", "orders", "payments", "reports"]


def _public_methods(obj):
    return {
        name
        for name, value in inspect.getmembers(type(obj))
        if callable(value) and not name.startswith("_")
    }


def test_same_resources_and_method_names():
    shop, _, _ = make_shop()
    ashop, _, _ = make_async_shop()
    assert _public_methods(shop) - {"close"} <= _public_methods(ashop)
    for name in RESOURCES:
        sync_res, async_res = getattr(shop, name), getattr(ashop, name)
        assert _public_methods(sync_res) == _public_methods(async_res), name
        for method in _public_methods(sync_res):
            sync_sig = inspect.signature(getattr(sync_res, method))
            async_sig = inspect.signature(getattr(async_res, method))
            assert list(sync_sig.parameters) == list(async_sig.parameters), f"{name}.{method}"
            for param in sync_sig.parameters:
                assert (
                    sync_sig.parameters[param].default == async_sig.parameters[param].default
                ), f"{name}.{method}({param})"


def test_constructor_signature_matches():
    sync_params = inspect.signature(Shop.__init__).parameters
    async_params = inspect.signature(AsyncShop.__init__).parameters
    assert list(sync_params) == list(async_params)
    for name in sync_params:
        assert sync_params[name].default == async_params[name].default


def test_every_method_hits_its_path():
    async def main():
        shop, rec, _ = make_async_shop()
        async with shop:
            assert isinstance(await shop.store(), models.StoreResponse)
            assert isinstance(await shop.attention(), models.Attention)
            assert isinstance(await shop.approvals.get(12), models.Approval)
            assert isinstance(await shop.categories.list(), models.CategoryPage)
            assert isinstance(await shop.items.list(q="x"), models.ItemPage)
            assert isinstance(await shop.items.get(10), models.ItemDetail)
            assert isinstance(await shop.stock.list(), models.StockPage)
            assert isinstance(await shop.orders.list(summary=True), models.OrderPage)
            assert isinstance(await shop.orders.get(100), models.OrderDetail)
            assert isinstance(await shop.payments.list(), models.PaymentPage)
            assert isinstance(await shop.reports.summary(days=90), models.SalesSummary)
        return rec

    rec = asyncio.run(main())
    assert [r.url.path[len(PREFIX):] for r in rec.requests] == [
        "/store",
        "/attention",
        "/approvals/12",
        "/categories",
        "/items",
        "/items/10",
        "/stock",
        "/orders",
        "/orders/100",
        "/payments",
        "/reports/summary",
    ]
    assert rec.requests[4].url.params["q"] == "x"
    assert rec.requests[7].url.params["summary"] == "true"
    assert rec.requests[10].url.params["days"] == "90"
    for request in rec.requests:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json"
        assert request.headers["user-agent"].startswith("apexai-shop-python/")


def test_pagination():
    def handler(request):
        cursor = request.url.params.get("cursor")
        base = SAMPLES["list_categories"]["data"][0]
        if cursor is None:
            return httpx.Response(200, json={"data": [dict(base, id=1)], "next_cursor": "c1"})
        return httpx.Response(200, json={"data": [dict(base, id=2)], "next_cursor": None})

    async def main():
        shop, rec, _ = make_async_shop(handler)
        ids = [c.id async for c in shop.categories.iter(limit=1)]
        return ids, rec

    ids, rec = asyncio.run(main())
    assert ids == [1, 2]
    assert dict(rec.requests[0].url.params) == {"limit": "1"}
    assert dict(rec.requests[1].url.params) == {"cursor": "c1"}


def test_retries_and_rate_limit():
    queue = [
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "4"}),
        httpx.Response(200, json=SAMPLES["get_store"]),
    ]

    async def main():
        shop, rec, sleeps = make_async_shop(lambda request: queue.pop(0))
        result = await shop.store()
        return result, rec, sleeps

    result, rec, sleeps = asyncio.run(main())
    assert result.store.tier == "business"
    assert sleeps == [0.5, 4.0]
    assert len(rec.requests) == 3


def test_rate_limit_exhausted():
    async def main():
        shop, _, sleeps = make_async_shop(
            lambda request: httpx.Response(429, headers={"Retry-After": "99"}), max_retries=1
        )
        with pytest.raises(RateLimitError) as info:
            await shop.store()
        return info.value, sleeps

    err, sleeps = asyncio.run(main())
    assert err.retry_after == 99.0
    assert sleeps == [30.0]


def test_transport_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    async def main():
        shop, rec, sleeps = make_async_shop(handler)
        with pytest.raises(ConnectionError) as info:
            await shop.store()
        return info.value, rec, sleeps

    err, rec, sleeps = asyncio.run(main())
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]
    assert TOKEN not in str(err)


def test_422_fields():
    async def main():
        shop, _, _ = make_async_shop(
            lambda request: httpx.Response(422, json={"error": "invalid_request", "fields": ["days"]})
        )
        with pytest.raises(BadRequestError) as info:
            await shop.reports.summary(days=5)
        return info.value

    err = asyncio.run(main())
    assert err.fields == ["days"]


def test_repr_hides_token_and_env_config(monkeypatch):
    monkeypatch.setenv("SHOP_API_URL", BASE_URL)
    monkeypatch.setenv("SHOP_API_TOKEN", TOKEN)

    async def main():
        async with AsyncShop() as shop:
            return repr(shop), shop._client

    text, client = asyncio.run(main())
    assert text == "AsyncShop(base_url='https://your-store.example/api/merchant/v1')"
    assert TOKEN not in text
    assert client.is_closed


def test_missing_token(monkeypatch):
    monkeypatch.delenv("SHOP_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SHOP_API_TOKEN"):
        AsyncShop(BASE_URL)
