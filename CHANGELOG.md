# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [0.3.0] - Unreleased

Writes, starting with categories. Requires a store whose merchant API takes changes.

### Added

- Writes send a JSON body and an `Idempotency-Key` header: a random key per call (or
  your own with `idempotency_key=`), the same on every retry of that call, so a retry
  never changes the store twice.
- Typed write results with the API's envelope fields (`status`, `replayed`, `noop`,
  `result`, `confirmation`, `approval`, `effect`, `next`) and the `request_id`:
  `Completed`, `ConfirmationRequired` (with `.confirm()`, which repeats the same call
  with the confirmation) and `PendingApproval`; `WriteResult` is their base class.
- Category writes on `Shop` and `AsyncShop` (scope `catalog:write`):
  `categories.create(title)`, `categories.update(category_id, title=, active=)`,
  `categories.move(category_id, to_index)` and `categories.delete(category_id,
  confirmation=)`, a two-step delete.
- Exceptions for refused writes: `ConflictError` (409, with `current`), `ChangedError`
  (409 `changed`, with a fresh bound `preview`), `ConfirmationInvalidError`,
  `StoreSuspendedError` (423), `DailyCapError` (429 `daily_cap`, a `RateLimitError`),
  `ApprovalRequiredError` (403, with `reasons`) and `WritesDisabledError` (403), the
  last two `PermissionDeniedError`s.
- Models for the write envelope: `WriteAnswer`, `CategoryAnswer`, `CategoryMoveAnswer`,
  `CategoryDeleteAnswer`, `CategoryMoved`, `CategoryDeleted`, `ConfirmationInfo`,
  `ApprovalInfo` and `Effect`.

### Changed

- The bundled contract (`contract/merchant-api-v1.json`) includes the category writes;
  the contract tests also check every write's body fields and `Idempotency-Key` header.
- Writes are retried on 409 `idempotency_in_progress`, 429 other than `daily_cap`, 502,
  503 (including `busy`), 504 and network errors, only when Retry-After is at most 30
  seconds. Retries of reads are unchanged.
- 409 responses raise `ConflictError` and 423 responses `StoreSuspendedError` instead
  of a plain `ShopError`.

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
