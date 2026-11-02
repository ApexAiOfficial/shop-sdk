"""Export orders from the last N days (at most 92) to CSV.

Needs a token with orders:read; buyer ids are filled in only with customers:read.

    python examples/orders_to_csv.py --days 30 --status paid > orders.csv
    python examples/orders_to_csv.py --days 7 --output week.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone

from apexai_shop import Shop

COLUMNS = ["id", "status", "total_stars", "created_at", "paid_at", "buyer_tg_user_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="how far back to go (1-92)")
    parser.add_argument(
        "--status",
        default="all",
        help="all, paid, refunded, processing or pending",
    )
    parser.add_argument("--output", help="file to write (default: standard output)")
    args = parser.parse_args()
    if not 1 <= args.days <= 92:
        parser.error("--days must be between 1 and 92")

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)

    out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        count = 0
        with Shop() as shop:
            for order in shop.orders.iter(status=args.status, since=since, until=until):
                writer.writerow(
                    [
                        order.id,
                        order.status,
                        order.total_stars,
                        order.created_at.isoformat(),
                        order.paid_at.isoformat() if order.paid_at else "",
                        order.buyer_tg_user_id if order.buyer_tg_user_id is not None else "",
                    ]
                )
                count += 1
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"{count} order(s) exported.", file=sys.stderr)


if __name__ == "__main__":
    main()
