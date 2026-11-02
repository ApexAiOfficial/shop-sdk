# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-02

### Added

- `verify_webhook(body, headers, secret)` for the store's signed webhooks: it checks the
  `Shop-Webhook-Signature` (`v1=` HMAC-SHA256 over `timestamp.body`, compared in constant
  time, any of several values may match), refuses timestamps more than 5 minutes away, reads
  headers without regard to case, and returns a `WebhookEvent`. `WebhookVerificationError`
  marks anything that is not genuine; `compute_signature` is exposed for tests.
- `contract/merchant-webhooks-v1.json`: the JSON Schema of every event (`order.paid`,
  `order.refunded`, `ticket.created`, `stock.low`, `webhook.test`) and tests that keep
  `WebhookEvent` in step with it.
- Example: a standard-library webhook receiver (`examples/webhook_receiver.py`).

## [0.1.0] - 2026-11-01

First release, for merchant API contract version 1.

### Added

- `Shop` (sync, httpx) and `AsyncShop` (async) clients with the same surface:
  `store()`, `attention()`, `categories`, `items`, `stock`, `orders`,
  `payments` and `reports.summary()`.
- `iter()` on every list resource, following `next_cursor`.
- Pydantic v2 models for every response schema; unknown fields are ignored
  and enum-like values are plain strings, for forward compatibility.
- Exceptions: `ShopError` and `AuthenticationError`, `PermissionDeniedError`,
  `NotFoundError`, `BadRequestError` (with `fields`), `RateLimitError` (with
  `retry_after`), `ServerError`, `ConnectionError`.
- Retries for 429 (honouring Retry-After, capped at 30 s), 502/503/504 and
  transport errors, with 0.5 s / 1 s / 2 s backoff.
- Configuration from `SHOP_API_URL` and `SHOP_API_TOKEN`.
- The OpenAPI contract and tests that keep the client in step with it.
- Examples: daily sales, low stock, orders to CSV.

[0.1.0]: https://github.com/ApexAiOfficial/shop-sdk/releases/tag/v0.1.0
