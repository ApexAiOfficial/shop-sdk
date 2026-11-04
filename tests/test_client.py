"""Synchronous client: configuration, requests, errors, retries and pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import apexai_shop
from apexai_shop import (
    AuthenticationError,
    BadRequestError,
    ConnectionError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    Shop,
    ShopError,
    models,
)
from conftest import BASE_URL, PREFIX, SAMPLES, TOKEN, make_shop, route


# --- configuration -----------------------------------------------------------


def test_reads_base_url_and_token_from_env(monkeypatch):
    monkeypatch.setenv("SHOP_API_URL", BASE_URL)
    monkeypatch.setenv("SHOP_API_TOKEN", TOKEN)
    shop = Shop()
    try:
        assert shop.base_url == BASE_URL.rstrip("/")
    finally:
        shop.close()


@pytest.mark.parametrize(
    "url",
    ["http://your-store.example/api/merchant/v1", "http://10.0.0.5:8081/api/merchant/v1", "ftp://store.example/", "store.example"],
)
def test_a_token_never_crosses_the_network_in_clear_text(url):
    with pytest.raises(ValueError, match="https://"):
        Shop(url, TOKEN)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8197/api/merchant/v1", "http://localhost:8197/api/merchant/v1", "http://[::1]:8197/api/merchant/v1",
     "http://dev.localhost:8197/api/merchant/v1"],
)
def test_plain_http_is_allowed_for_a_store_on_this_machine(url):
    shop = Shop(url, TOKEN)
    try:
        assert shop.base_url == url
    finally:
        shop.close()


def test_missing_base_url_names_env_var(monkeypatch):
    monkeypatch.delenv("SHOP_API_URL", raising=False)
    monkeypatch.setenv("SHOP_API_TOKEN", TOKEN)
    with pytest.raises(ValueError, match="SHOP_API_URL"):
        Shop()


def test_missing_token_names_env_var(monkeypatch):
    monkeypatch.setenv("SHOP_API_URL", BASE_URL)
    monkeypatch.delenv("SHOP_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SHOP_API_TOKEN"):
        Shop()


def test_explicit_arguments_win_over_env(monkeypatch):
    monkeypatch.setenv("SHOP_API_URL", "https://other.example/api/merchant/v1")
    monkeypatch.setenv("SHOP_API_TOKEN", "other")
    shop, rec, _ = make_shop()
    shop.store()
    assert rec.requests[0].url.host == "your-store.example"
    assert rec.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_rejects_non_http_base_url():
    with pytest.raises(ValueError):
        Shop("your-store.example/api/merchant/v1", TOKEN)


def test_context_manager_closes_owned_client():
    with Shop(BASE_URL, TOKEN) as shop:
        client = shop._client
    assert client.is_closed


def test_close_leaves_passed_client_open():
    shop, _, _ = make_shop()
    shop.close()
    assert not shop._client.is_closed


def test_wrong_http_client_type():
    with pytest.raises(TypeError):
        Shop(BASE_URL, TOKEN, http_client=httpx.AsyncClient())


# --- request building ------------------------------------------------------


def test_headers():
    shop, rec, _ = make_shop()
    shop.store()
    headers = rec.requests[0].headers
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["user-agent"] == f"apexai-shop-python/{apexai_shop.__version__}"
    assert headers["accept"] == "application/json"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://your-store.example/api/merchant/v1",
        "https://your-store.example/api/merchant/v1/",
        "https://your-store.example/api/merchant/v1//",
    ],
)
def test_base_url_with_or_without_trailing_slash(base_url):
    shop, rec, _ = make_shop(base_url=base_url)
    shop.items.get(10)
    assert str(rec.requests[0].url) == "https://your-store.example/api/merchant/v1/items/10"


def test_base_url_with_port_and_deeper_prefix():
    def handler(request):
        assert request.url.path == "/shop/api/merchant/v1/stock"
        return httpx.Response(200, json=SAMPLES["list_stock"])

    shop, rec, _ = make_shop(handler, base_url="https://your-store.example:8443/shop/api/merchant/v1/")
    shop.stock.list()
    assert rec.requests[0].url.port == 8443


def test_every_method_hits_its_path():
    shop, rec, _ = make_shop()
    assert isinstance(shop.store(), models.StoreResponse)
    assert isinstance(shop.attention(), models.Attention)
    assert isinstance(shop.categories.list(), models.CategoryPage)
    assert isinstance(shop.items.list(), models.ItemPage)
    assert isinstance(shop.items.get(10), models.ItemDetail)
    assert isinstance(shop.stock.list(), models.StockPage)
    assert isinstance(shop.orders.list(), models.OrderPage)
    assert isinstance(shop.orders.get(100), models.OrderDetail)
    assert isinstance(shop.payments.list(), models.PaymentPage)
    assert isinstance(shop.reports.summary(), models.SalesSummary)
    paths = [r.url.path[len(PREFIX):] for r in rec.requests]
    assert paths == [
        "/store",
        "/attention",
        "/categories",
        "/items",
        "/items/10",
        "/stock",
        "/orders",
        "/orders/100",
        "/payments",
        "/reports/summary",
    ]
    assert {r.method for r in rec.requests} == {"GET"}


def test_none_params_are_omitted():
    shop, rec, _ = make_shop()
    shop.items.list()
    shop.orders.list()
    assert rec.requests[0].url.query == b""
    assert rec.requests[1].url.query == b""


def test_reports_summary_sends_default_days():
    shop, rec, _ = make_shop()
    shop.reports.summary()
    shop.reports.summary(days=7)
    assert rec.requests[0].url.params["days"] == "30"
    assert rec.requests[1].url.params["days"] == "7"


def test_items_query_params():
    shop, rec, _ = make_shop()
    shop.items.list(q="guide pro", category=3, status="on_sale", limit=10)
    params = rec.requests[0].url.params
    assert dict(params) == {"q": "guide pro", "category": "3", "status": "on_sale", "limit": "10"}


def test_orders_datetimes_are_iso8601_and_bools_lowercase():
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    until = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=2)))
    shop, rec, _ = make_shop()
    shop.orders.list(status="paid", since=since, until=until, buyer=42, summary=True)
    params = rec.requests[0].url.params
    assert params["since"] == "2026-10-01T00:00:00+00:00"
    assert params["until"] == "2026-10-31T23:59:59+02:00"
    assert params["summary"] == "true"
    assert params["buyer"] == "42"
    assert params["status"] == "paid"
    assert datetime.fromisoformat(params["since"]) == since


def test_summary_false_is_sent_as_false():
    shop, rec, _ = make_shop()
    shop.orders.list(summary=False)
    assert rec.requests[0].url.params["summary"] == "false"


def test_path_params_are_escaped():
    def handler(request):
        return httpx.Response(404, json={"error": "not_found"})

    shop, rec, _ = make_shop(handler)
    with pytest.raises(NotFoundError):
        shop.items.get("1/../../store")
    assert rec.requests[0].url.raw_path.startswith(b"/api/merchant/v1/items/1%2F")


def test_parses_datetimes():
    shop, _, _ = make_shop()
    order = shop.orders.get(100)
    assert order.created_at == datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    assert order.refunded_at is None


# --- errors ------------------------------------------------------------------


def _respond(status, body=None, headers=None):
    def handler(request):
        if body is None:
            return httpx.Response(status, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return handler


@pytest.mark.parametrize(
    "status,cls",
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (422, BadRequestError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (418, ShopError),
    ],
)
def test_error_mapping(status, cls):
    shop, _, _ = make_shop(
        _respond(status, {"error": "some_code", "message": "Human words."}, {"x-request-id": "req-1"}),
        max_retries=0,
    )
    with pytest.raises(cls) as info:
        shop.store()
    err = info.value
    assert isinstance(err, ShopError)
    assert err.status == status
    assert err.code == "some_code"
    assert err.message == "Human words."
    assert err.request_id == "req-1"
    assert "req-1" in str(err)


def test_403_insufficient_scope():
    shop, _, _ = make_shop(_respond(403, {"error": "insufficient_scope"}))
    with pytest.raises(PermissionDeniedError) as info:
        shop.orders.list()
    assert info.value.code == "insufficient_scope"
    assert info.value.message  # a default sentence when the server sends none


def test_422_fields():
    shop, _, _ = make_shop(_respond(422, {"error": "invalid_request", "fields": ["limit", "cursor"]}))
    with pytest.raises(BadRequestError) as info:
        shop.items.list(limit=1000)
    assert info.value.status == 422
    assert info.value.code == "invalid_request"
    assert info.value.fields == ["limit", "cursor"]
    assert "limit" in str(info.value)


def test_400_has_empty_fields():
    shop, _, _ = make_shop(_respond(400, {"error": "window_too_long", "message": "At most 92 days."}))
    with pytest.raises(BadRequestError) as info:
        shop.orders.list()
    assert info.value.fields == []
    assert info.value.message == "At most 92 days."


def test_error_without_json_body():
    def handler(request):
        return httpx.Response(500, text="<html>oops</html>")

    shop, _, _ = make_shop(handler)
    with pytest.raises(ServerError) as info:
        shop.store()
    assert info.value.code == "http_500"


def test_invalid_success_body():
    shop, _, _ = make_shop(_respond(200, {"unexpected": True}))
    with pytest.raises(ShopError) as info:
        shop.store()
    assert info.value.code == "invalid_response"


def test_non_json_success_body():
    def handler(request):
        return httpx.Response(200, text="not json")

    shop, _, _ = make_shop(handler)
    with pytest.raises(ShopError) as info:
        shop.store()
    assert info.value.code == "invalid_response"


# --- retries -----------------------------------------------------------------


def _sequence(*responses):
    queue = list(responses)

    def handler(request):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def ok(op="get_store"):
    return httpx.Response(200, json=SAMPLES[op])


@pytest.mark.parametrize("status", [502, 503, 504])
def test_retries_gateway_errors_with_backoff(status):
    shop, rec, sleeps = make_shop(_sequence(httpx.Response(status), httpx.Response(status), ok()))
    assert shop.store().store.name == "Demo Store"
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_backoff_sequence_with_more_retries():
    shop, rec, sleeps = make_shop(
        _sequence(*[httpx.Response(503)] * 4, ok()), max_retries=4
    )
    shop.store()
    assert sleeps == [0.5, 1.0, 2.0, 2.0]


def test_gives_up_after_max_retries():
    shop, rec, sleeps = make_shop(_sequence(*[httpx.Response(503)] * 3))
    with pytest.raises(ServerError) as info:
        shop.store()
    assert info.value.status == 503
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_500_is_not_retried():
    shop, rec, sleeps = make_shop(_sequence(httpx.Response(500), ok()))
    with pytest.raises(ServerError):
        shop.store()
    assert len(rec.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(status):
    shop, rec, sleeps = make_shop(_sequence(httpx.Response(status, json={"error": "x"}), ok()))
    with pytest.raises(ShopError):
        shop.store()
    assert len(rec.requests) == 1
    assert sleeps == []


def test_429_honours_retry_after():
    shop, rec, sleeps = make_shop(
        _sequence(httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "rate_limited"}), ok())
    )
    shop.store()
    assert sleeps == [3.0]
    assert len(rec.requests) == 2


def test_429_retry_after_is_capped_at_30_seconds():
    shop, _, sleeps = make_shop(_sequence(httpx.Response(429, headers={"Retry-After": "120"}), ok()))
    shop.store()
    assert sleeps == [30.0]


def test_429_retry_after_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=10)
    header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    shop, _, sleeps = make_shop(_sequence(httpx.Response(429, headers={"Retry-After": header}), ok()))
    shop.store()
    assert 0 <= sleeps[0] <= 11


def test_429_without_retry_after_backs_off():
    shop, _, sleeps = make_shop(_sequence(httpx.Response(429), ok()))
    shop.store()
    assert sleeps == [0.5]


def test_429_exhausted_raises_rate_limit_error():
    resp = lambda: httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate_limited"})
    shop, rec, sleeps = make_shop(_sequence(resp(), resp(), resp()))
    with pytest.raises(RateLimitError) as info:
        shop.store()
    assert info.value.retry_after == 2.0
    assert info.value.code == "rate_limited"
    assert sleeps == [2.0, 2.0]


def test_transport_errors_are_retried_then_raise_connection_error():
    shop, rec, sleeps = make_shop(
        _sequence(httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.ConnectError("boom"))
    )
    with pytest.raises(ConnectionError) as info:
        shop.store()
    assert isinstance(info.value, ShopError)
    assert info.value.status is None
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_transport_error_then_success():
    shop, _, sleeps = make_shop(_sequence(httpx.ConnectError("boom"), ok()))
    assert shop.store().api_version == "1"
    assert sleeps == [0.5]


def test_max_retries_zero():
    shop, rec, sleeps = make_shop(_sequence(httpx.Response(503), ok()), max_retries=0)
    with pytest.raises(ServerError):
        shop.store()
    assert len(rec.requests) == 1
    assert sleeps == []


def test_negative_max_retries_rejected():
    with pytest.raises(ValueError):
        Shop(BASE_URL, TOKEN, max_retries=-1)


# --- pagination --------------------------------------------------------------


def _paged_items(pages):
    """Serve ``pages`` (lists of ids) linked by cursors c1, c2, ..."""

    def handler(request):
        cursor = request.url.params.get("cursor")
        index = 0 if cursor is None else int(cursor[1:])
        ids = pages[index]
        base = SAMPLES["list_items"]["data"][0]
        nxt = f"c{index + 1}" if index + 1 < len(pages) else None
        return httpx.Response(
            200, json={"data": [dict(base, id=i) for i in ids], "next_cursor": nxt}
        )

    return handler


def test_iter_follows_next_cursor_and_sends_only_the_cursor():
    shop, rec, _ = make_shop(_paged_items([[1, 2], [3], [4, 5]]))
    ids = [item.id for item in shop.items.iter(q="guide", status="on_sale", limit=2)]
    assert ids == [1, 2, 3, 4, 5]
    assert dict(rec.requests[0].url.params) == {"q": "guide", "status": "on_sale", "limit": "2"}
    assert dict(rec.requests[1].url.params) == {"cursor": "c1"}
    assert dict(rec.requests[2].url.params) == {"cursor": "c2"}


def test_iter_is_lazy():
    shop, rec, _ = make_shop(_paged_items([[1], [2]]))
    it = shop.items.iter()
    assert rec.requests == []
    assert next(it).id == 1
    assert len(rec.requests) == 1


def test_iter_stops_on_repeated_cursor():
    def handler(request):
        return httpx.Response(200, json={"data": SAMPLES["list_stock"]["data"], "next_cursor": "same"})

    shop, rec, _ = make_shop(handler)
    assert len(list(shop.stock.iter())) == 2
    assert len(rec.requests) == 2


def test_orders_iter_sends_filters_then_cursor():
    page1 = dict(SAMPLES["list_orders"], next_cursor="o1")
    page2 = SAMPLES["list_orders"]
    shop, rec, _ = make_shop(_sequence(httpx.Response(200, json=page1), httpx.Response(200, json=page2)))
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    orders = list(shop.orders.iter(status="paid", since=since))
    assert len(orders) == 2
    assert dict(rec.requests[0].url.params) == {"status": "paid", "since": since.isoformat()}
    assert dict(rec.requests[1].url.params) == {"cursor": "o1"}


@pytest.mark.parametrize("resource", ["categories", "stock", "payments"])
def test_other_iterators(resource):
    shop, _, _ = make_shop()
    rows = list(getattr(shop, resource).iter())
    assert len(rows) == 1


# --- secrecy -----------------------------------------------------------------


def test_repr_shows_only_base_url():
    shop, _, _ = make_shop()
    assert repr(shop) == "Shop(base_url='https://your-store.example/api/merchant/v1')"
    assert TOKEN not in repr(shop)
    assert TOKEN not in str(shop)
    assert TOKEN not in repr(vars(shop).get("categories"))


@pytest.mark.parametrize(
    "handler",
    [
        _respond(401, {"error": "unauthorized"}),
        _respond(422, {"error": "invalid_request", "fields": ["cursor"]}),
        _respond(429, {"error": "rate_limited"}, {"Retry-After": "1"}),
        _respond(503),
        _sequence(*[httpx.ConnectError("refused")] * 3),
    ],
)
def test_token_never_in_errors(handler):
    shop, _, _ = make_shop(handler)
    with pytest.raises(ShopError) as info:
        shop.store()
    for text in (str(info.value), repr(info.value), str(info.value.args)):
        assert TOKEN not in text
