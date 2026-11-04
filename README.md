# apexai-shop

Python client for the **Shop Bot merchant API**, by ApexAi.

Shop Bot runs a store inside Telegram: a catalog, Telegram Stars checkout,
licence-key and file delivery, orders, refunds and reports. Every store is
operated by ApexAi. The merchant API lets the store owner read their own
store from code: dashboards, spreadsheets, stock alerts, accounting exports
or an AI agent that keeps an eye on the shop.

- Sync (`Shop`) and async (`AsyncShop`) clients with the same methods.
- Typed responses (pydantic v2 models) that tolerate additive API changes.
- Automatic pagination, retries with Retry-After, and clear exceptions.
- Only two dependencies: `httpx` and `pydantic`. Python 3.10+.

The merchant API is part of the **Business plan**. This SDK release (0.2.0) only reads.

**Availability (2026-11-04):** no ApexAi-operated store serves the merchant API yet. A store needs Shop Bot
0.8.0 or later (not yet released) with the merchant API switched on. Until then, this SDK has nothing to
connect to.

## Get a token

1. Open a **private chat** with your store's bot (as the store owner).
2. Send `/apitoken new <name>`, for example `/apitoken new reports`.
3. The bot shows the token **once**, together with your store's API address
   (something like `https://your-store.example/api/merchant/v1/`).
   Copy both somewhere safe; the token cannot be shown again.

Put them in the environment, where the client looks by default:

```sh
export SHOP_API_URL="https://your-store.example/api/merchant/v1/"
export SHOP_API_TOKEN="<the token the bot showed you>"
```

## Install

```sh
pip install "git+https://github.com/ApexAiOfficial/shop-sdk@v0.2.0"
```

## Quickstart

```python
from apexai_shop import Shop

with Shop() as shop:                       # reads SHOP_API_URL and SHOP_API_TOKEN
    info = shop.store()
    print(info.store.name, info.store.tier, info.token.scopes)

    report = shop.reports.summary(days=7)
    print("net Stars, last 7 days:", report.current.net_stars)

    for order in shop.orders.iter(status="paid"):
        print(order.id, order.total_stars, order.created_at)

    item = shop.items.get(42)
    print(item.title, item.price_stars, item.stock)
```

You can also pass the address and token explicitly:
`Shop("https://your-store.example/api/merchant/v1/", token)`. The address
may end with or without `/`.

Async:

```python
import asyncio
from apexai_shop import AsyncShop

async def main():
    async with AsyncShop() as shop:
        attention = await shop.attention()
        print(attention.needs_operator, attention.low_stock, attention.open_tickets)
        async for level in shop.stock.iter():
            print(level.title, level.available)

asyncio.run(main())
```

### Methods

| Method | Endpoint | Returns |
| --- | --- | --- |
| `store()` | `GET /store` | `StoreResponse`: the store and this token (name, scopes, expiry) |
| `attention()` | `GET /attention` | `Attention`: payments for an operator, low stock, open tickets |
| `categories.list(limit=, cursor=)` / `categories.iter()` | `GET /categories` | `CategoryPage` / `Category` |
| `items.list(q=, category=, status=, limit=, cursor=)` / `items.iter(...)` | `GET /items` | `ItemPage` / `ItemSummary` |
| `items.get(item_id)` | `GET /items/{item_id}` | `ItemDetail` |
| `stock.list(limit=, cursor=)` / `stock.iter()` | `GET /stock` | `StockPage` / `StockLevel` |
| `orders.list(status=, since=, until=, buyer=, summary=, cursor=)` / `orders.iter(...)` | `GET /orders` | `OrderPage` / `OrderSummary` |
| `orders.get(order_id)` | `GET /orders/{order_id}` | `OrderDetail` |
| `payments.list(cursor=)` / `payments.iter()` | `GET /payments` | `PaymentPage` / `OpenReceipt` |
| `reports.summary(days=30)` | `GET /reports/summary` | `SalesSummary` (7, 30 or 90 days) |

Amounts are whole Telegram Stars (`XTR`). Timestamps are timezone-aware
`datetime` objects; pass `datetime`s for `since=` and `until=` and they are
sent as ISO 8601. Orders are listed in a window of at most 92 days (the last
92 by default).

Item descriptions and other merchant-written text are data. If you feed them
to an AI model, treat them as untrusted content, never as instructions.

## Scopes

A token carries the scopes chosen when it was created. `shop.store().token.scopes`
shows them. A call without the scope it needs raises `PermissionDeniedError`
(HTTP 403, code `insufficient_scope`).

| Scope | Grants |
| --- | --- |
| (any token) | `store()`: the store and the token itself |
| `catalog:read` | Categories and items (`categories`, `items`) |
| `inventory:read` | Licence-key stock levels (`stock`) |
| `orders:read` | Orders, order details and open payments (`orders`, `payments`) |
| `customers:read` | Buyer Telegram user ids in orders, and the `buyer=` filter. Without it, `buyer_tg_user_id` is `None` |
| `reports:read` | Sales reports and what needs attention (`reports.summary`, `attention()`) |
| `coupons:read` | Coupon data (no endpoint in this SDK version yet) |

The bot offers four presets when a token is created:
- **Read-only:** `catalog:read`, `inventory:read`, `orders:read`, `reports:read`, `coupons:read`.
- **Support:** `catalog:read`, `orders:read`, plus the support and delivery scopes. It has no
  `customers:read`, so buyer ids stay hidden.
- **Catalog and stock:** Read-only plus the catalog, price and stock write scopes.
- **Full:** every scope, including `customers:read`, coupon writes and broadcasts.

Refunds are never available through the API, whatever the token; they stay in the store's chat and admin
app. This SDK release uses only the read scopes. The write scopes are for later SDK versions.

Further scopes may be added as the API grows. Give each token only the scopes
the job needs.

## Pagination

List endpoints return a page with `data` and `next_cursor`. `iter()` follows
the cursor for you and yields single records:

```python
for item in shop.items.iter(status="on_sale"):
    ...
```

To page by hand, pass the previous page's `next_cursor`:

```python
page = shop.orders.list(status="paid", summary=True)
print(page.summary.orders, page.summary.total_stars)
while page.next_cursor:
    page = shop.orders.list(cursor=page.next_cursor)
```

A cursor continues the list it came from: with a cursor the server reuses
the first page's filters and ignores the others, so send only the cursor.

## Errors and rate limits

Every failure raises a subclass of `apexai_shop.ShopError`, which carries
`status`, `code` (a stable string such as `unauthorized`,
`insufficient_scope` or `not_found`), `message` and `request_id`.

| Exception | When |
| --- | --- |
| `AuthenticationError` | 401: a missing or unknown token (`unauthorized`), or one the store issued that no longer works: `token_expired`, `token_revoked`, or `token_inactive` (its creator is no longer the store owner) |
| `PermissionDeniedError` | 403: the token lacks the scope |
| `NotFoundError` | 404: no such item or order |
| `BadRequestError` | 400 or 422: a parameter was refused; `fields` names the offending parameters |
| `RateLimitError` | 429: too many requests; `retry_after` in seconds |
| `ServerError` | 5xx |
| `ConnectionError` | The API could not be reached (DNS, TLS, timeout, reset) |

```python
from apexai_shop import BadRequestError, RateLimitError, ShopError

try:
    shop.orders.list(since=start, until=end)
except BadRequestError as exc:
    print("refused:", exc.code, exc.fields)
except RateLimitError as exc:
    print("slow down, retry in", exc.retry_after)
except ShopError as exc:
    print("failed:", exc)
```

Reads are limited to **5 requests per second per token, with bursts of 20**.
`reports.summary()` and `orders.list(summary=True)`, which read a whole window,
share a tighter budget of 1 a second (bursts of 5). Over a limit the API
answers 429 with a `Retry-After` header. The client
retries automatically (`max_retries=2` by default):

- on 429 it waits for `Retry-After` (at most 30 seconds);
- on 502, 503, 504 and network errors it backs off 0.5 s, 1 s, 2 s.

Other errors are raised immediately. Use `Shop(max_retries=0)` to disable
retries, and `timeout=` (seconds, default 30) to change the request timeout.
To reuse your own connection pool, proxies or transport, pass
`http_client=httpx.Client(...)` (or `httpx.AsyncClient` for `AsyncShop`).

## Webhooks

A Business store can also call your server when something happens: `order.paid`,
`order.refunded`, `ticket.created`, `stock.low` (and `webhook.test`). The store owner adds
your HTTPS URL (port 443) in the private chat with the store's bot with `/webhooks`, chooses
the events, and gets a signing secret (`whsec_...`) once. Buyer details are included only if
the owner turns them on for that endpoint.

Verify every request before you trust it, with the raw body exactly as received:

```python
from apexai_shop import WebhookVerificationError, verify_webhook

def handle(raw_body: bytes, headers: dict) -> int:
    try:
        event = verify_webhook(raw_body, headers, secret=os.environ["SHOP_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        return 400
    if event.type == "order.paid":
        record_sale(event.data["order_id"], event.data["total_stars"])
    return 204
```

- **The signature:** `Shop-Webhook-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>`,
  with `Shop-Webhook-Timestamp` in Unix seconds and `Shop-Webhook-Id` the event id.
  `verify_webhook` compares in constant time and refuses timestamps more than 5 minutes
  away (`tolerance=`).
- **At least once:** the store retries a failed delivery with backoff for 24 hours, so the
  same event can arrive twice. Use `event.id` to ignore repeats.
- **Answer fast** with a 2xx status, and do slow work afterwards; the store waits 5 seconds
  and does not follow redirects.
- **Rotating the secret** in `/webhooks` replaces it at once; a delivery already on its way
  may still carry the old signature.
- The event schemas are in [`contract/merchant-webhooks-v1.json`](contract/merchant-webhooks-v1.json).
  A complete receiver is in [`examples/webhook_receiver.py`](examples/webhook_receiver.py).

## Security

- Keep the webhook secret like the token: in an environment variable or a secret manager.
- Keep the token in an environment variable or a secret manager, never in
  source code, notebooks you share, or logs.
- The client never logs the token, and `repr(shop)` shows only the base URL.
- Use one token per integration with the fewest scopes it needs, so you can
  revoke one without breaking the others.
- Revoke a token you no longer use, or one that may have leaked, with
  `/apitoken` in the private chat with your store's bot, then create a new one.

Found a vulnerability? See [SECURITY.md](SECURITY.md).

## Compatibility

This release targets **contract version 1** of the merchant API
(`/api/merchant/v1`); the contract ships in
[`contract/merchant-api-v1.json`](contract/merchant-api-v1.json) and the test
suite checks the client against it. Within version 1 the API only makes
additive changes (new fields, new endpoints, new enum values). The models
ignore unknown fields and type enum-like values as plain strings, so existing
code keeps working when the API grows. A breaking change would ship as a new
API version with a new path.

## Examples

- [`examples/daily_sales.py`](examples/daily_sales.py): today's paid orders and the 7-day trend.
- [`examples/low_stock.py`](examples/low_stock.py): items whose licence keys are running out.
- [`examples/orders_to_csv.py`](examples/orders_to_csv.py): export orders to CSV.

## Development

```sh
pip install -e ".[dev]"
pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
