"""Preview and confirm deletion of one category or item.

Needs a token with catalog:write.

    python examples/safe_delete.py category 7
    python examples/safe_delete.py item 42
"""

from __future__ import annotations

import argparse
from typing import TypeVar

from apexai_shop import Completed, ConfirmationRequired, Shop, WriteResult

T = TypeVar("T")


def confirm_delete(result: WriteResult[T]) -> int:
    if not isinstance(result, ConfirmationRequired):
        print(f"Delete did not produce a preview (status: {result.status}).")
        return 1

    print("Preview:")
    print(result.effect.summary)
    if input("Delete? [y/N] ").strip().lower() != "y":
        print("Delete cancelled.")
        return 0

    completed = result.confirm()
    if isinstance(completed, Completed):
        print("Delete completed.")
        return 0
    print(f"Delete did not complete (status: {completed.status}).")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("category", "item"), help="object to delete")
    parser.add_argument("id", type=int, help="category or item id")
    args = parser.parse_args()

    with Shop() as shop:
        if args.kind == "category":
            return confirm_delete(shop.categories.delete(args.id))
        return confirm_delete(shop.items.delete(args.id))


if __name__ == "__main__":
    raise SystemExit(main())
