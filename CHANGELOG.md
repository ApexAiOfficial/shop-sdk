# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [0.3.0] - Unreleased

Completes the merchant API write surface: catalog, prices, coupons, licence
keys, support, fulfilment, broadcasts and product delivery. Requires a store
whose merchant API takes changes.

### Security

- A plain `http://` base URL is refused unless it points at this machine (`localhost`, `127.0.0.1`, `::1`, `*.localhost`), so a token never crosses the network in clear text.

### Added

- Store-changing writes send a JSON body and an `Idempotency-Key` header: a random key per call (or
  your own with `idempotency_key=`), the same on every retry of that call, so a retry
  never changes the store twice.
- Key staging is intentionally non-idempotent in the contract. It sends no
  `Idempotency-Key` and is not retried after an ambiguous failure, avoiding duplicate
  open stages.
- Every error from an idempotent write carries the call's `idempotency_key`. A `ConnectionError`,
  `ServerError` or `invalid_response` does not say whether the change was made; repeating
  the same call with that key answers the stored result or makes the change once.
- Typed write results with the API's envelope fields (`status`, `replayed`, `noop`,
  `result`, `confirmation`, `approval`, `effect`, `next`), the `request_id`, and the
  parsed envelope as `answer` (a pydantic model, for `model_dump()`): `Completed`,
  `ConfirmationRequired` (with `.confirm()`, which repeats the same call with the
  confirmation) and `PendingApproval`; `WriteResult` is their base class. `AsyncShop`
  previews are `AsyncConfirmationRequired`, whose `confirm()` is typed as awaitable.
  `Completed.result` and a preview's `confirmation` and `effect` are never `None`: an
  answer without them is an `invalid_response` error.
- Owner approvals can be read with `approvals.get(approval_id)`, returning the
  new `Approval` model. `PendingApproval.wait(timeout=, poll=)` polls through
  `Shop` until a final status and raises `ApprovalTimeoutError` only when it is
  still pending at the timeout; `AsyncPendingApproval.wait()` is awaitable and
  has the same behavior. A queued change is not made unless the owner taps
  Approve in Telegram within 15 minutes and must never be reported as done from
  its initial 202 response.
- `repr()` never shows a confirmation id, `ConfirmationInfo`'s included.
- A `confirmation_invalid` that follows an attempt whose answer was lost says so: that
  attempt may have spent the confirmation, and nothing was changed.
- Category writes on `Shop` and `AsyncShop` (scope `catalog:write`):
  `categories.create(title)`, `categories.update(category_id, title=, active=)`,
  `categories.move(category_id, to_index)` and `categories.delete(category_id,
  confirmation=)`, a two-step delete; `confirmation=` takes the preview's
  `confirmation` or its id.
- Item writes (scope `catalog:write`; `pricing:write` too for a price):
  `items.create(category_id, title, price_stars, description=)` (created hidden),
  `items.update(item_id, version=, title=, description=, price_stars=, active=)`,
  `items.move(item_id, to_index)` and `items.delete(item_id, confirmation=)`, a
  two-step delete whose preview says whether the item is deleted or archived.
- Broadcasts (scope `broadcasts:write`): `broadcasts.send(text)` always queues
  owner approval before any buyer receives it; `broadcasts.cancel(id)` stops
  messages not sent yet and returns a typed `BroadcastInfo` result.
- Variants and bundles: `variants.add`, `variants.update`, `bundles.create` and
  `bundles.update`, with per-variant optimistic versions and exactly one bundle
  component added or removed per update. Deep variant price cuts can wait for
  owner approval. Adding an item to a bundle needs `pricing:write`, and
  `bundles.update` answers `PendingApproval` when an add leaves the bundle
  priced under half of what its items sell for apart.
- Product files: `items.request_file(item_id, variant_id=)` prompts the owner in
  Telegram. It carries a fresh idempotency key per call but is never retried
  automatically, because every call creates a new prompt.
- Bulk prices: `items.reprice(changes, confirmation=)` previews and confirms up
  to 100 versioned item-price changes together, or waits for owner approval
  when a cut is deep.
- Coupons: `coupons.list(status=, code=, limit=, cursor=)` and `coupons.iter()` (scope
  `coupons:read`); `coupons.create(code, pct_off, max_uses=, per_user_max_uses=,
  expires_at=)` and `coupons.update(code, active=, max_uses=, per_user_max_uses=,
  expires_at=, confirmation=)` (scope `coupons:write`). Switching off a used coupon
  previews first.
- Support tickets: `tickets.list(include_closed=, limit=)`, `tickets.iter()` and
  `tickets.get(ticket_id)` (scope `support:read`), plus
  `tickets.reply(ticket_id, body)` and `tickets.close(ticket_id)` (scope
  `support:write`), with typed ticket, thread, reply and close models. Buyer-written
  subjects and messages are explicitly marked as untrusted data; buyer ids are `None`
  without `customers:read`.
- Fulfilment: `orders.restore_downloads(order_id, downloads=)` (scope
  `fulfillment:write`) restores a paid order's file downloads and download window,
  with a typed `DownloadsRestored` result and the per-order daily cap exposed as
  `DailyCapError`.
- Ticket replies and download restores keep the SDK's idempotency-key behavior and
  the store's 10-minute duplicate protection. Replies over 4,000 characters are
  refused rather than cut; replies containing contact details require the owner's
  approval.
- Licence-key inventory: `stock.item(item_id)` (scope `inventory:read`), plus
  `stock.stage`, `stock.commit`, `stock.void` and `stock.release` (scope
  `inventory:write`) with typed, key-free results. Voiding unsold keys and releasing
  checkout holds use the same two-step confirmation flow as other destructive writes.
- `stock.import_keys(item_id, text=... or path=...)` reads explicit text or a UTF-8
  file, splits it below 1,000 keys, 60,000 characters and a 60 KiB JSON body per
  batch, stages and commits each batch, and returns secret-free per-batch and total
  counts. A refusal reports how many batches committed. Turning on key delivery
  requires explicit `enable_key_delivery=True`; `ConfirmKeyDeliveryError` never opts
  in on the caller's behalf.
- Licence-key text is excluded from result, error and summary representations and is
  redacted from staging failures and logs.
- Models: `Coupon`, `CouponUses`, `CouponPage`, `CouponAnswer`, `ItemAnswer`,
  `ItemMoveAnswer`, `ItemMoved`, `ItemDeleteAnswer`, `ItemDeleted`.
- Final write models: `BroadcastInfo`, `Bundle`, `Variant`, `FileRequested`,
  `RepriceChange`, `Repriced`, `RepriceResult` and their answer models.
- Support and fulfilment models: `TicketSummary`, `TicketPage`, `TicketMessage`,
  `TicketDetail`, `TicketReplied`, `TicketReplyAnswer`, `TicketClosed`,
  `TicketCloseAnswer`, `DownloadsRestored` and `DownloadsRestoreAnswer`.
- Inventory models: `StockDetail`, `StockCounts`, `StockHolds`, `KeyStage`,
  `StagedImport`, `KeyImport`, `KeysVoided`, `HoldsReleased` and their write-answer
  models, plus the SDK's `KeyImportBatch` and `KeyImportSummary`.
- Exceptions for refused writes: `ConflictError` (409, with `current`), `ChangedError`
  (409 `changed`, with a fresh bound `preview`), `ConfirmationInvalidError`,
  `StoreSuspendedError` (423), `DailyCapError` (429 `daily_cap`, a `RateLimitError`),
  `ApprovalRequiredError` (403, with `reasons`) and `WritesDisabledError` (403), the
  last two `PermissionDeniedError`s.
- Models for the write envelope: `WriteAnswer`, `CategoryAnswer`, `CategoryMoveAnswer`,
  `CategoryDeleteAnswer`, `CategoryMoved`, `CategoryDeleted`, `ConfirmationInfo`,
  `ApprovalInfo` and `Effect`.

### Changed

- The bundled contracts are synchronized to revision 2026-11-04. The merchant
  API contract adds the approval read and documents 202 `pending_approval` for
  broadcasts, deep price cuts (new variants and bundle adds included), large
  coupons and guarded support replies;
  the contract tests also check every write's body fields and declared idempotency header,
  and that every nullable body field is optional (the client leaves out `None`).
- CI type-checks the package, the examples and the README's write examples with
  `mypy --strict` (`mypy` joins the `dev` extra).
- Idempotent writes are retried on 409 `idempotency_in_progress`, 429 other than `daily_cap`, 502,
  503 (including `busy`), 504 and network errors, only when Retry-After is at most 30
  seconds (backing off 0.5 s, 1 s, 2 s when there is none). Retries of reads are
  unchanged.
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
