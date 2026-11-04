"""Static surface checks for the 0.4.0 reviews, custom-order, membership and label methods. Never run."""

from typing import AsyncIterator, Iterator, List

from apexai_shop import (
    AsyncConfirmationRequired,
    AsyncShop,
    Completed,
    ConfirmationRequired,
    Shop,
    WriteResult,
    WrongStateError,
    models,
)


def sync_surface(shop: Shop) -> None:
    page: models.ReviewPage = shop.reviews.list(status="queue", item_id=1, rating=5)
    rows: Iterator[models.ReviewRow] = shop.reviews.iter(status="all")
    queued: Iterator[models.ReviewRow] = shop.reviews.queue(limit=10)
    review: models.ReviewDetail = shop.reviews.get(1)
    published: WriteResult[models.ReviewState] = shop.reviews.publish(1)
    withheld: WriteResult[models.ReviewState] = shop.reviews.withhold(1, "hide")
    responded: WriteResult[models.ReviewResponseResult] = shop.reviews.respond(1, "Thanks!")
    orders: models.CustomOrderPage = shop.custom_orders.list(status="open")
    order_rows: Iterator[models.CustomOrderRow] = shop.custom_orders.iter()
    order: models.CustomOrderDetail = shop.custom_orders.get(1)
    asked: WriteResult[models.CustomOrderState] = shop.custom_orders.clarify(1, "Which size?")
    rejected: WriteResult[models.CustomOrderState] = shop.custom_orders.reject(1, "Not our kind of work.")
    plans: models.MembershipPlanList = shop.membership_plans.list()
    plan: models.MembershipPlanRow = shop.membership_plans.get(1)
    priced: WriteResult[models.MembershipPlanRow] = shop.membership_plans.set_price(1, 300)
    switched: WriteResult[models.MembershipPlanRow] = shop.membership_plans.set_active(1, False)
    labelled: WriteResult[models.VersionLabel] = shop.items.set_version_label(1, "2.0")
    label: str | None = shop.items.get(1).version_label
    if isinstance(priced, ConfirmationRequired):
        confirmed: WriteResult[models.MembershipPlanRow] = priced.confirm()
        if isinstance(confirmed, Completed):
            price: int = confirmed.result.price_stars
            print(price)
    try:
        shop.custom_orders.reject(1, "Not our kind of work.")
    except WrongStateError as exc:
        status: str | None = exc.current_status
        print(status)
    history: List[models.ReviewEvent] = review.history
    print(page, rows, queued, published, withheld, responded, orders, order_rows, order)
    print(asked, rejected, plans, plan, switched, labelled, label, history)


async def async_surface(shop: AsyncShop) -> None:
    page: models.ReviewPage = await shop.reviews.list(status="queue")
    rows: AsyncIterator[models.ReviewRow] = shop.reviews.iter(status="all")
    queued: AsyncIterator[models.ReviewRow] = shop.reviews.queue()
    review: models.ReviewDetail = await shop.reviews.get(1)
    published: WriteResult[models.ReviewState] = await shop.reviews.publish(1)
    withheld: WriteResult[models.ReviewState] = await shop.reviews.withhold(1, "reject")
    responded: WriteResult[models.ReviewResponseResult] = await shop.reviews.respond(1, "Thanks!")
    orders: models.CustomOrderPage = await shop.custom_orders.list()
    order_rows: AsyncIterator[models.CustomOrderRow] = shop.custom_orders.iter(status="all")
    order: models.CustomOrderDetail = await shop.custom_orders.get(1)
    asked: WriteResult[models.CustomOrderState] = await shop.custom_orders.clarify(1, "Which size?")
    rejected: WriteResult[models.CustomOrderState] = await shop.custom_orders.reject(1, "Not ours.")
    plans: models.MembershipPlanList = await shop.membership_plans.list()
    plan: models.MembershipPlanRow = await shop.membership_plans.get(1)
    priced: WriteResult[models.MembershipPlanRow] = await shop.membership_plans.set_price(1, 300)
    switched: WriteResult[models.MembershipPlanRow] = await shop.membership_plans.set_active(1, True)
    labelled: WriteResult[models.VersionLabel] = await shop.items.set_version_label(1, "")
    if isinstance(withheld, AsyncConfirmationRequired):
        confirmed: WriteResult[models.ReviewState] = await withheld.confirm()
        print(confirmed)
    print(page, rows, queued, review, published, responded, orders, order_rows, order)
    print(asked, rejected, plans, plan, priced, switched, labelled)
