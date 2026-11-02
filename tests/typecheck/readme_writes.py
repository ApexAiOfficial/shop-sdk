"""The README's "Changing the store" examples, unchanged, for the type checker.

CI runs ``mypy --strict`` on this file; ``tests/test_readme.py`` checks that every
Python block of that README section appears here verbatim. Never imported or run.
"""

# --- the write example -------------------------------------------------------

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

# --- the async example -------------------------------------------------------

from apexai_shop import AsyncConfirmationRequired, AsyncShop, Completed


async def delete_category(category_id: int) -> None:
    async with AsyncShop() as shop:
        preview = await shop.categories.delete(category_id)
        if isinstance(preview, AsyncConfirmationRequired):
            print(preview.effect.summary)
            done = await preview.confirm()
            if isinstance(done, Completed):
                print("deleted", done.result.category_id)

# --- your own key and retrying after a lost answer ---------------------------

shop = Shop()

shop.categories.create("Guides", idempotency_key="import-2026-11-02-guides")

from apexai_shop import ConnectionError, ServerError

try:
    created = shop.categories.create("Guides")
except (ConnectionError, ServerError) as exc:
    created = shop.categories.create("Guides", idempotency_key=exc.idempotency_key)

# --- owner approval waits ---------------------------------------------------

from apexai_shop import AsyncPendingApproval, PendingApproval, models


def wait_for_approval(pending: PendingApproval[object]) -> models.Approval:
    return pending.wait(timeout=900, poll=2)


async def wait_for_approval_async(pending: AsyncPendingApproval[object]) -> models.Approval:
    return await pending.wait(timeout=900, poll=2)
