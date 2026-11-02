"""List key-selling items whose available licence keys are at or below a threshold.

Needs a token with inventory:read. Exits with status 1 when something is low,
so it can drive an alert from cron or CI.

    python examples/low_stock.py --threshold 5
"""

from __future__ import annotations

import argparse
import sys

from apexai_shop import Shop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threshold", type=int, default=5, help="alert at or below this many keys")
    args = parser.parse_args()

    with Shop() as shop:
        low = [level for level in shop.stock.iter() if level.available <= args.threshold]

    if not low:
        print(f"No item has {args.threshold} or fewer keys available.")
        return 0
    low.sort(key=lambda level: level.available)
    print(f"{len(low)} item(s) with {args.threshold} or fewer keys available:")
    for level in low:
        print(
            f"  #{level.item_id} {level.title}: {level.available} available, "
            f"{level.reserved} reserved, {level.sold} sold"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
