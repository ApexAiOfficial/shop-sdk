"""Reprice one item using its current version and a confirmation preview.

Needs a token with catalog:read and pricing:write.

    python examples/reprice.py 42 250
    python examples/reprice.py 42 100 --wait
"""

from __future__ import annotations

import argparse
import json

from apexai_shop import (
    ApprovalTimeoutError,
    Completed,
    ConfirmationRequired,
    ConflictError,
    PendingApproval,
    Shop,
    WriteResult,
    models,
)


def report_result(
    result: WriteResult[models.RepriceResult], *, wait: bool, timeout: float
) -> int:
    if isinstance(result, Completed):
        changed = result.result.items[0]
        print(f"Item #{changed.item_id} now costs {changed.price_stars} Stars.")
        return 0

    if isinstance(result, PendingApproval):
        print("The owner must approve this price change in Telegram.")
        if not wait:
            return 0
        try:
            approval = result.wait(timeout=timeout)
        except ApprovalTimeoutError:
            print("The price change is still waiting for approval.")
            return 1
        print(f"Approval finished with status: {approval.status}")
        return 0 if approval.status == "done" else 1

    if isinstance(result, ConfirmationRequired):
        print(result.effect.summary)
        if input("Apply this price change? [y/N] ").strip().lower() != "y":
            print("Price change cancelled.")
            return 0
        return report_result(result.confirm(), wait=wait, timeout=timeout)

    print(f"Price change did not complete (status: {result.status}).")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("item_id", type=int, help="item to reprice")
    parser.add_argument("price_stars", type=int, help="new price in Telegram Stars")
    parser.add_argument("--wait", action="store_true", help="wait for an owner approval")
    parser.add_argument(
        "--timeout", type=float, default=900.0, help="approval wait timeout in seconds"
    )
    args = parser.parse_args()
    if args.price_stars < 1:
        parser.error("price_stars must be at least 1")
    if args.timeout < 0:
        parser.error("--timeout must be 0 or greater")

    with Shop() as shop:
        item = shop.items.get(args.item_id)
        change = models.RepriceChange(
            item_id=item.id, version=item.version, price_stars=args.price_stars
        )
        try:
            result = shop.items.reprice([change])
            return report_result(result, wait=args.wait, timeout=args.timeout)
        except ConflictError as exc:
            print("The item changed after it was read; no price change was made.")
            if exc.current is not None:
                print("Current item:")
                print(json.dumps(exc.current, indent=2, sort_keys=True, default=str))
            else:
                print("Fetch the item again before retrying.")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
