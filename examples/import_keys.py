"""Import licence keys from a UTF-8 file without displaying key text.

Needs a token with inventory:write.

    python examples/import_keys.py 42 licence-keys.txt
    python examples/import_keys.py 42 licence-keys.txt --enable-key-delivery
"""

from __future__ import annotations

import argparse

from apexai_shop import ConfirmKeyDeliveryError, Shop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("item_id", type=int, help="item that will receive the keys")
    parser.add_argument("path", help="UTF-8 file containing one key per line")
    parser.add_argument(
        "--enable-key-delivery",
        action="store_true",
        help="agree to permanently enable key delivery when importing the first keys",
    )
    args = parser.parse_args()

    try:
        with Shop() as shop:
            summary = shop.stock.import_keys(
                args.item_id,
                path=args.path,
                enable_key_delivery=args.enable_key_delivery,
            )
    except ConfirmKeyDeliveryError as exc:
        committed = exc.batches_committed or 0
        print(f"Import stopped after {committed} completed batch(es).")
        print("Key delivery is not enabled for this item.")
        print("After the owner agrees, rerun with --enable-key-delivery.")
        return 1

    for batch in summary.batches:
        print(
            f"Batch {batch.batch}: {batch.keys} read, {batch.added} added, "
            f"{batch.duplicates} duplicates, {batch.too_long} too long, "
            f"{batch.available} available"
        )
    print(
        f"Total: {summary.total_added} added, "
        f"{summary.total_duplicates} duplicates, {summary.total_too_long} too long; "
        f"{summary.available} available"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
