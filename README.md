# apexai-shop

Python client for the **Shop Bot merchant API**, by ApexAi.

Shop Bot runs a store inside Telegram: a catalog, Telegram Stars checkout,
licence-key and file delivery, orders, refunds and reports. Every store is
operated by ApexAi. The merchant API lets the store owner read and change
their own store from code: dashboards, spreadsheets, stock alerts,
accounting exports, catalog tools or an AI agent that helps run the shop.

- Sync (`Shop`) and async (`AsyncShop`) clients with the same methods.
- Typed responses (pydantic v2 models) that tolerate additive API changes.
- Automatic pagination, retries with Retry-After, and clear exceptions.
- Safe writes: an idempotency key per change, previews you confirm, typed results.
- Only two dependencies: `httpx` and `pydantic`. Python 3.10+.

The merchant API is part of the **Business plan**. Reading needs a token with
read scopes; changing the store needs a write scope such as `catalog:write`
(see [Changing the store](#changing-the-store)).

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
pip install "git+https://github.com/ApexAiOfficial/shop-sdk@v0.1.0"
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
| `approvals.get(approval_id)` | `GET /approvals/{approval_id}` | `Approval`: the final or pending outcome of a queued owner approval |
| `categories.list(limit=, cursor=)` / `categories.iter()` | `GET /categories` | `CategoryPage` / `Category` |
| `items.list(q=, category=, status=, limit=, cursor=)` / `items.iter(...)` | `GET /items` | `ItemPage` / `ItemSummary` |
| `items.get(item_id)` | `GET /items/{item_id}` | `ItemDetail` |
| `stock.list(limit=, cursor=)` / `stock.iter()` | `GET /stock` | `StockPage` / `StockLevel` |
| `stock.item(item_id)` | `GET /items/{item_id}/stock` | `StockDetail`: counts, holds and voidable stock, never key text |
| `stock.stage(item_id, keys_text)` | `POST /items/{item_id}/keys/stage` | `Completed` with `KeyStage` counts and a staged-import id |
| `stock.commit(item_id, staged_import, enable_key_delivery=)` | `POST /items/{item_id}/keys/commit` | `Completed` with `KeyImport` counts |
| `stock.import_keys(item_id, text=... or path=..., enable_key_delivery=)` | stage + commit in safe batches | `KeyImportSummary` with per-batch and total counts |
| `stock.void(item_id, confirmation=)` | `POST /items/{item_id}/keys/void` | `ConfirmationRequired`, then `Completed` with `KeysVoided` |
| `stock.release(item_id, confirmation=)` | `POST /items/{item_id}/keys/release` | `ConfirmationRequired`, then `Completed` with `HoldsReleased` |
| `orders.list(status=, since=, until=, buyer=, summary=, cursor=)` / `orders.iter(...)` | `GET /orders` | `OrderPage` / `OrderSummary` |
| `orders.get(order_id)` | `GET /orders/{order_id}` | `OrderDetail` |
| `orders.restore_downloads(order_id, downloads=)` | `POST /orders/{order_id}/restore-downloads` | `Completed` with `DownloadsRestored` |
| `tickets.list(include_closed=, limit=)` / `tickets.iter(...)` | `GET /tickets` | `TicketPage` / `TicketSummary` |
| `tickets.get(ticket_id)` | `GET /tickets/{ticket_id}` | `TicketDetail` with the whole thread |
| `tickets.reply(ticket_id, body)` | `POST /tickets/{ticket_id}/replies` | `Completed` with `TicketReplied` |
| `tickets.close(ticket_id)` | `POST /tickets/{ticket_id}/close` | `Completed` with `TicketClosed` |
| `payments.list(cursor=)` / `payments.iter()` | `GET /payments` | `PaymentPage` / `OpenReceipt` |
| `reports.summary(days=30)` | `GET /reports/summary` | `SalesSummary` (7, 30 or 90 days) |
| `categories.create(title)` | `POST /categories` | `Completed` with the new `Category` |
| `categories.update(category_id, title=, active=)` | `PATCH /categories/{category_id}` | `Completed` with the `Category` |
| `categories.move(category_id, to_index)` | `POST /categories/{category_id}/move` | `Completed` with `CategoryMoved` |
| `categories.delete(category_id, confirmation=)` | `POST /categories/{category_id}/delete` | `ConfirmationRequired`, then `Completed` with `CategoryDeleted` |
| `items.create(category_id, title, price_stars, description=)` | `POST /items` | `Completed` with the new, hidden `ItemDetail` |
| `items.update(item_id, version=, title=, description=, price_stars=, active=)` | `PATCH /items/{item_id}` | `Completed` with the `ItemDetail` |
| `items.move(item_id, to_index)` | `POST /items/{item_id}/move` | `Completed` with `ItemMoved` |
| `items.delete(item_id, confirmation=)` | `POST /items/{item_id}/delete` | `ConfirmationRequired`, then `Completed` with `ItemDeleted` |
| `items.request_file(item_id, variant_id=)` | `POST /items/{item_id}/file-request` | `Completed` with `FileRequested`; sends the owner a new Telegram prompt |
| `items.reprice(changes, confirmation=)` | `POST /reprice` | `ConfirmationRequired`, then `Completed` with `RepriceResult`, or `PendingApproval` for deep cuts |
| `variants.add(item_id, label, price_stars)` | `POST /items/{item_id}/variants` | `Completed` with the new `Variant`, or `PendingApproval` |
| `variants.update(variant_id, version=, label=, price_stars=, note=, active=)` | `PATCH /variants/{variant_id}` | `Completed` with the `Variant`, or `PendingApproval` |
| `bundles.create(category_id, title, price_stars)` | `POST /bundles` | `Completed` with the new, hidden bundle item |
| `bundles.update(bundle_id, add_item_id= or remove_item_id=)` | `PATCH /bundles/{bundle_id}` | `Completed` with the `Bundle`, or `PendingApproval` when an add leaves the bundle under half of its items' own prices |
| `broadcasts.send(text)` | `POST /broadcasts` | `PendingApproval`; no message is sent until the owner approves |
| `broadcasts.cancel(broadcast_id)` | `POST /broadcasts/{broadcast_id}/cancel` | `Completed` with `BroadcastInfo` |
| `coupons.list(status=, code=, limit=, cursor=)` / `coupons.iter(...)` | `GET /coupons` | `CouponPage` / `Coupon` |
| `coupons.create(code, pct_off, max_uses=, per_user_max_uses=, expires_at=)` | `POST /coupons` | `Completed` with the `Coupon` |
| `coupons.update(code, active=, max_uses=, per_user_max_uses=, expires_at=, confirmation=)` | `PATCH /coupons/{code}` | `Completed` with the `Coupon`; switching off a used coupon previews first |
| `reviews.list(status=, item_id=, rating=, limit=, cursor=)` / `reviews.iter(...)` | `GET /reviews` | `ReviewPage` / `ReviewRow`; `status` is `queue` (default), `public`, `hidden`, `removed` or `all` |
| `reviews.queue(item_id=, rating=, limit=)` | `GET /reviews?status=queue` | Every `ReviewRow` waiting for moderation, oldest first |
| `reviews.get(review_id)` | `GET /reviews/{review_id}` | `ReviewDetail` with the store's response, the history (roles, never ids) and the item's `Rating` |
| `reviews.publish(review_id)` | `POST /reviews/{review_id}/publish` | `Completed` with `ReviewState`, or `PendingApproval` when the review holds a link or contact details |
| `reviews.withhold(review_id, action, confirmation=)` | `POST /reviews/{review_id}/withhold` | `action` is `reject` or `hide`; `ConfirmationRequired` first when the item's rating counts the review, then `Completed` with `ReviewState` |
| `reviews.respond(review_id, body)` | `POST /reviews/{review_id}/response` | `Completed` with `ReviewResponseResult` (its length, never its text), or `PendingApproval` with contact details |
| `custom_orders.list(status=, limit=, cursor=)` / `custom_orders.iter(...)` | `GET /custom-orders` | `CustomOrderPage` / `CustomOrderRow`; `status` is `open` (default), `all` or one status |
| `custom_orders.get(request_id)` | `GET /custom-orders/{request_id}` | `CustomOrderDetail`: brief, latest answer, question or reason, quote, notice |
| `custom_orders.clarify(request_id, question, confirmation=)` | `POST /custom-orders/{request_id}/clarify` | `ConfirmationRequired`, then `Completed` with `CustomOrderState`; messages the buyer. `PendingApproval` with contact details |
| `custom_orders.reject(request_id, reason, confirmation=)` | `POST /custom-orders/{request_id}/reject` | `ConfirmationRequired`, then `Completed` with `CustomOrderState`; tells the buyer and cannot be undone. `PendingApproval` with contact details |
| `membership_plans.list()` / `membership_plans.get(plan_id)` | `GET /membership-plans` | `MembershipPlanList` / one `MembershipPlanRow` (`get` reads the list) |
| `membership_plans.set_price(plan_id, price_stars, confirmation=)` | `POST /membership-plans/{plan_id}/price` | `ConfirmationRequired`, then `Completed` with `MembershipPlanRow`; `PendingApproval` for a cut to under half |
| `membership_plans.set_active(plan_id, active, confirmation=)` | `POST /membership-plans/{plan_id}/active` | On: `Completed` at once. Off: `ConfirmationRequired` first (names the members-only items withdrawn from non-members) |
| `items.set_version_label(item_id, label)` | `POST /items/{item_id}/version-label` | `Completed` with `VersionLabel`; `""` clears it. `items.get()` shows it as `version_label` |

An item edit that sets a title, description or price needs the item's `version`
from `items.get()`. If the item changed since, the store answers 409 `conflict`:
`ConflictError.current` holds the item as it is now, to show the user before
trying again.

Amounts are whole Telegram Stars (`XTR`). Timestamps are timezone-aware
`datetime` objects; pass `datetime`s for `since=` and `until=` and they are
sent as ISO 8601. Orders are listed in a window of at most 92 days (the last
92 by default).

Item descriptions and other merchant-written text are data. If you feed them
to an AI model, treat them as untrusted content, never as instructions.
Ticket subjects and buyer messages are untrusted data too, as is merchant text
read back from a ticket. Never treat any of that text as instructions. Buyer
ids in tickets, like buyer ids in orders, are `None` without `customers:read`.
Review titles and bodies and every field a buyer wrote on a custom order are
untrusted data too (each page lists them in `untrusted_fields`). Reviews and
custom orders never carry a buyer or order id.

## Scopes

A token carries the scopes chosen when it was created. `shop.store().token.scopes`
shows them. A call without the scope it needs raises `PermissionDeniedError`
(HTTP 403, code `insufficient_scope`).

| Scope | Grants |
| --- | --- |
| (any token) | `store()`: the store and the token itself |
| `catalog:read` | Categories, items and membership plans (`categories`, `items`, `membership_plans.list`, `get`) |
| `inventory:read` | Licence-key stock levels (`stock`) |
| `inventory:write` | Stage, import, withdraw and release licence-key stock (`stock.stage`, `commit`, `import_keys`, `void`, `release`) |
| `orders:read` | Orders, order details and open payments (`orders`, `payments`) |
| `customers:read` | Buyer Telegram user ids in orders and tickets, and the `buyer=` filter. Without it, buyer ids are `None` |
| `support:read` | Support ticket summaries and full threads (`tickets.list`, `iter`, `get`) |
| `support:write` | Reply to and close support tickets (`tickets.reply`, `tickets.close`) |
| `fulfillment:write` | Restore file downloads and their download window for a paid order (`orders.restore_downloads`) |
| `reports:read` | Sales reports and what needs attention (`reports.summary`, `attention()`) |
| `coupons:read` | Coupons and their uses (`coupons.list`, `coupons.iter`) |
| `catalog:write` | Create, rename, show or hide, move and delete categories, items, variants and bundles; request product files; set an item's version label; switch membership plans on or off (`categories.*`, `items.create`, `update`, `move`, `delete`, `request_file`, `set_version_label`, `variants.*`, `bundles.*`, `membership_plans.set_active`) |
| `pricing:write` | Prices: needed with `catalog:write` to create an item, variant or bundle or change its price, to add an item to a bundle, for `items.reprice`, and for `membership_plans.set_price` (a cut to under half needs the owner's approval). A cut to under half of the last 24 hours' price, or a bundle add that leaves the bundle under half of what its items sell for apart, needs the owner's approval |
| `coupons:write` | Create coupons and change them (`coupons.create`, `coupons.update`). A large coupon (50% off or more, no limit, or more than 1,000 uses) needs the owner's approval |
| `broadcasts:write` | Send broadcasts for owner approval and cancel broadcasts (`broadcasts.send`, `broadcasts.cancel`) |
| `reviews:read` | Buyers' reviews, the moderation queue and each review's history (`reviews.list`, `iter`, `queue`, `get`) |
| `reviews:write` | Publish, reject or hide reviews and set the store's response (`reviews.publish`, `withhold`, `respond`). Publishing a review, or a response, with a link or contact details needs the owner's approval |
| `custom_orders:read` | Custom-order requests and their details (`custom_orders.list`, `iter`, `get`) |
| `custom_orders:write` | Ask a custom order's buyer a question or reject the request; both message the buyer (`custom_orders.clarify`, `reject`). A text with a link or contact details needs the owner's approval |

The bot offers presets when a token is created: **read-only** (`catalog:read`,
`inventory:read`, `orders:read`, `reports:read`, `coupons:read`, `reviews:read`,
`custom_orders:read`) and presets that add write scopes; `/apitoken` in the private
chat lists them. A token minted before the store took changes through the API has no
write scope: mint a new one for a job that changes the store. A token is never
widened after it is minted, so one made before the review and custom-order scopes
existed lacks them: mint a new one for a job that reads or moderates reviews or
answers custom orders. Membership plans and version labels need only catalog
scopes, so older tokens with those scopes can use them.

Further scopes arrive with the operations that need them. Give each token only
the scopes the job needs.

## Importing licence keys

Licence keys are secrets. Keys sent through an AI conversation reach the AI
provider, so prefer this SDK or the bot's `/addkeys` with a file. The SDK never
returns key text and keeps it out of result and error representations and logs.

Pass either `text=` (one key per line) or `path=` (a UTF-8 text file), never
both. `import_keys` splits the input into batches within the API's 1,000-key,
60,000-character and request-size limits, then stages and commits each batch in
order:

```python
with Shop() as shop:
    imported = shop.stock.import_keys(42, path="licence-keys.txt")
    print(imported.total_added, imported.total_duplicates)
```

If a batch is refused, the exception's `batches_committed` says how many prior
batches completed. Importing the first keys into an item permanently turns on
key delivery; the store refuses with `ConfirmKeyDeliveryError` unless you
explicitly pass `enable_key_delivery=True` after agreeing to that change.

## Changing the store

Writes change the store as its owner would in the bot. Every write returns
one of three results, each with the API's envelope fields (`status`,
`replayed`, `noop`, `result`, `confirmation`, `approval`, `effect`, `next`):

| Result | When | What to do |
| --- | --- | --- |
| `Completed` | `done` (HTTP 200) | The change is made; `result` is the changed object. `noop` is true when nothing needed to change |
| `ConfirmationRequired` | `confirmation_required` (HTTP 202) | Nothing changed yet. Show the user `effect.summary`, and call `.confirm()` only after they agree |
| `PendingApproval` | `pending_approval` (HTTP 202) | Nothing changed yet; the change waits for the store owner to tap Approve in Telegram. Never report it as done |

A status newer than this client comes back as a plain `WriteResult` (the base
class of all three), so check the class before you use `result`:

```python
from apexai_shop import Completed, ConfirmationRequired, Shop

with Shop() as shop:
    created = shop.categories.create("Guides")
    if not isinstance(created, Completed):
        raise SystemExit(f"Not created yet: {created.next}")
    guides = created.result
    shop.categories.update(guides.id, title="Handbooks")
    shop.categories.move(guides.id, 0)               # first in the list

    preview = shop.categories.delete(guides.id)      # two steps: preview first
    if isinstance(preview, ConfirmationRequired):
        print(preview.effect.summary)                # show the user what will happen
        if input("Delete? [y/N] ") == "y":
            done = preview.confirm()                 # the same call, plus the confirmation
            if isinstance(done, Completed):
                print("deleted", done.result.category_id)
```

With `AsyncShop`, a preview is an `AsyncConfirmationRequired` (also a
`ConfirmationRequired`) whose `confirm()` you await:

```python
from apexai_shop import AsyncConfirmationRequired, AsyncShop, Completed


async def delete_category(category_id: int) -> None:
    async with AsyncShop() as shop:
        preview = await shop.categories.delete(category_id)
        if isinstance(preview, AsyncConfirmationRequired):
            print(preview.effect.summary)
            done = await preview.confirm()
            if isinstance(done, Completed):
                print("deleted", done.result.category_id)
```

**Confirmations.** A two-step write (deleting a category, for example) answers
first with a preview: the `effect` and a single-use `confirmation` that expires
at `confirmation.expires_at` and is only valid for the same operation and
arguments. `.confirm()` repeats the call with that confirmation through the
client that made the preview, so call it before the client is closed. To
confirm later, pass `confirmation=preview.confirmation` (or its `id`) to the
same method with the same arguments. If the effect changed in the meantime the
API refuses with `ChangedError`, whose `preview` is a fresh
`ConfirmationRequired` to show and confirm again (with `AsyncShop`, an
`AsyncConfirmationRequired`: `await err.preview.confirm()`). The confirmation id is never
shown by `repr()`; `result.answer` holds the whole envelope as a pydantic model
(`result.answer.model_dump(mode="json")`), id included, to return as data.

**Owner approvals.** Broadcasts, deep price cuts (a new variant or a bundle add
included), large coupons, and ticket replies that contain contact details can
answer `PendingApproval`. The change is queued but is not made unless the
store owner taps **Approve** in Telegram within 15 minutes; never report it
as done from the 202 answer. Call `pending.wait(timeout=900, poll=2)` to poll
through the originating `Shop` and return the final `Approval`, or use
`shop.approvals.get(pending.approval.id)`
directly. Rejected, expired, changed and failed approvals are returned normally
so callers can branch on `approval.status`; an approval that is still pending at
the timeout raises `ApprovalTimeoutError`. With `AsyncShop`, the result is an
`AsyncPendingApproval` and `await pending.wait(...)` provides the same behavior.

**Idempotency.** Every store-changing write sends an `Idempotency-Key` header:
a random key per call, the same on each of the client's own retries. If a retry
reaches the store after the first attempt already made the change, the API answers with the
stored result (`replayed` is true) instead of changing the store twice. Keys are
kept for 24 hours; the same key with a different body is refused (422
`idempotency_key_reused`). To retry safely across processes or restarts, pass
your own key, 16 to 64 letters, digits, `-` or `_`:

```python
shop.categories.create("Guides", idempotency_key="import-2026-11-02-guides")
```

Staging licence-key text is the contract's exception: each stage is new and
does not take an idempotency key. The SDK does not automatically retry staging
after an ambiguous failure, avoiding duplicate open stages. A product-file
request does carry a key, but it is also never retried automatically: every
call sends the owner a new Telegram prompt. If it fails without an answer,
make a new call with the default fresh key; do not reuse the failed call's key.

**When a write fails without an answer.** A `ConnectionError`, a `ServerError`
or an `invalid_response` error on a write does not tell you whether the change
was made. Every error from an idempotent write carries the call's
`idempotency_key`: except for `items.request_file`, repeat the same call with
it, and the API answers the stored result if the change was made, or makes it
once if it was not. A new call without that key is a new change (two
`create("Guides")` calls create two categories):

```python
from apexai_shop import ConnectionError, ServerError

try:
    created = shop.categories.create("Guides")
except (ConnectionError, ServerError) as exc:
    created = shop.categories.create("Guides", idempotency_key=exc.idempotency_key)
```

For a confirmation, call `preview.confirm()` again: a confirmation that already
made its change answers the stored result (`replayed`). If it answers
`ConfirmationInvalidError` instead, nothing was changed; ask for a new preview.

**Arguments.** `None` leaves a field as it is (`update(category_id, title=None,
active=False)` only hides the category). Text you send becomes store content
that buyers see; check what an AI agent wants to write before it does.

**Support replies.** A reply must be 1 to 4,000 characters; the API refuses an
overlong body rather than cutting it. A reply containing contact details such
as a link, handle, email address or phone number is refused with
`ApprovalRequiredError`; nothing is queued, so the store owner must approve
and make that reply through the owner workflow. The same reply within 10
minutes is deduplicated by the store, and the SDK also sends its usual
`Idempotency-Key`. The rolling 24-hour caps are 50 replies per token and 10
per ticket.

**Download restores.** Restores use the same idempotency and 10-minute
duplicate protection. The rolling cap is three restores per order in 24 hours,
regardless of which token made them; the token's own write caps also apply.

**Retries for writes.** Idempotent writes are retried, with the same key, on network
errors, 502/503/504 (503 `busy` means another change holds what this one needs),
429 other than `daily_cap` (such as `rate_limited`) and 409
`idempotency_in_progress`. They wait for Retry-After when it is at most 30
seconds (a longer one ends the retries and raises the error at once) and, when
there is no Retry-After, back off 0.5 s, 1 s, 2 s. They are never retried on
429 `daily_cap`: a token has a rolling 24-hour allowance of changes, and
`DailyCapError.retry_after` says when it has room again.

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
The ticket list is deliberately bounded by `limit` (at most 100) and has no
cursor; `tickets.iter()` yields that one bounded result.

## Errors and rate limits

Every failure raises a subclass of `apexai_shop.ShopError`, which carries
`status`, `code` (a stable string such as `unauthorized`,
`insufficient_scope` or `not_found`), `message` and `request_id`. Errors a
write raises also carry its `idempotency_key` (see
[When a write fails without an answer](#changing-the-store)).

| Exception | When |
| --- | --- |
| `AuthenticationError` | 401: a missing or unknown token (`unauthorized`), or one the store issued that no longer works: `token_expired`, `token_revoked`, or `token_inactive` (its creator is no longer the store owner) |
| `PermissionDeniedError` | 403: the token lacks the scope |
| `ApprovalRequiredError` | 403 `approval_required`: refused because the change needs the owner's approval; nothing was queued, so ask the owner to make it in the bot; `reasons` says why (a `PermissionDeniedError`). A `PendingApproval` result, by contrast, is queued and waits for the owner's Approve |
| `ApprovalTimeoutError` | `PendingApproval.wait()` reached its timeout while the approval was still pending; the queued change may still be approved later |
| `WritesDisabledError` | 403 `writes_disabled`: changes through the API are off for this store (a `PermissionDeniedError`) |
| `NotFoundError` | 404: no such item, order or category |
| `BadRequestError` | 400 or 422: a parameter was refused (for writes also `nothing_to_change`, `not_empty`, `idempotency_key_reused`); `fields` names the offending parameters |
| `ConflictError` | 409: the change conflicts with the store as it is now; for `conflict`, `current` holds the current object |
| `WrongStateError` | 409 `wrong_state`: the review or custom order is not in a state this change applies to; `current` or, for a custom order, `current_status` says where it is now (a `ConflictError`) |
| `NotPublishableError` | 409 `not_publishable`: the review was withdrawn (its order refunded or its buyer forgotten) and can never be published (a `ConflictError`) |
| `ChangedError` | 409 `changed`: the effect changed since the preview; `preview` is a fresh `ConfirmationRequired` (a `ConflictError`) |
| `ConfirmationInvalidError` | 409 `confirmation_invalid`: unknown, expired, used, or for other arguments; nothing was changed, so call again without it for a new preview (a `ConflictError`) |
| `StoreSuspendedError` | 423 `store_suspended`: the store is suspended |
| `RateLimitError` | 429: too many requests; `retry_after` in seconds |
| `DailyCapError` | 429 `daily_cap`: the token's 24-hour allowance of changes is spent; never retried (a `RateLimitError`) |
| `ServerError` | 5xx; on a write the change may have been made: repeat the call with `idempotency_key=exc.idempotency_key` |
| `ConnectionError` | The API could not be reached (DNS, TLS, timeout, reset); on a write, as for `ServerError` |

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

Writes follow the rules in [Changing the store](#changing-the-store). Other
errors are raised immediately. Use `Shop(max_retries=0)` to disable
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
  revoke one without breaking the others. Give a write scope only to code
  that must change the store.
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
- [`examples/reprice.py`](examples/reprice.py): safely reprice an item and handle owner approval.
- [`examples/import_keys.py`](examples/import_keys.py): import licence keys without displaying their text.
- [`examples/safe_delete.py`](examples/safe_delete.py): preview and confirm a category or item deletion.
- [`examples/reply_tickets.py`](examples/reply_tickets.py): list open tickets and reply from a file.
- [`examples/coupon_campaign.py`](examples/coupon_campaign.py): create a limited, expiring coupon campaign.
- [`examples/webhook_receiver.py`](examples/webhook_receiver.py): receive and verify webhook events.

## Development

```sh
pip install -e ".[dev]"
pytest
mypy          # strict: the package, the examples and the README's write examples
```

## License

Apache-2.0. See [LICENSE](LICENSE).
