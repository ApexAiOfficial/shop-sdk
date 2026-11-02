"""Print today's paid orders and the sales trend for the last 7 days.

Needs a token with orders:read and reports:read.

    export SHOP_API_URL="https://your-store.example/api/merchant/v1/"
    export SHOP_API_TOKEN="..."
    python examples/daily_sales.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from apexai_shop import Shop


def main() -> None:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with Shop() as shop:
        store = shop.store().store
        page = shop.orders.list(status="paid", since=midnight, until=now, summary=True)
        totals = page.summary
        report = shop.reports.summary(days=7)

    print(f"{store.name}: {now:%Y-%m-%d} (UTC)")
    if totals is not None:
        print(f"  paid orders today: {totals.orders}, {totals.total_stars} Stars")

    current, previous = report.current, report.previous
    change = current.net_stars - previous.net_stars
    print(
        f"  last 7 days: {current.paid_orders} paid orders, net {current.net_stars} Stars "
        f"({change:+d} vs the 7 days before)"
    )
    print(f"  net Stars per {report.bucket_days}-day bucket: {report.series}")
    for top in current.top_items[:5]:
        print(f"    {top.title}: {top.quantity} sold, {top.revenue_stars} Stars")


if __name__ == "__main__":
    main()
