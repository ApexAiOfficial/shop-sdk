"""Create a limited coupon campaign with an expiry date.

Needs a token with coupons:write. Large coupons wait for owner approval.

    python examples/coupon_campaign.py SPRING20 20
    python examples/coupon_campaign.py VIP50 50 --max-uses 100
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from apexai_shop import ApprovalTimeoutError, Completed, PendingApproval, Shop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("code", help="coupon code")
    parser.add_argument("pct_off", type=int, help="percentage discount")
    parser.add_argument("--max-uses", type=int, default=100, help="campaign-wide use limit")
    parser.add_argument(
        "--per-user-max-uses", type=int, default=1, help="use limit for each buyer"
    )
    parser.add_argument("--days", type=int, default=30, help="days until the coupon expires")
    parser.add_argument(
        "--timeout", type=float, default=900.0, help="owner approval timeout in seconds"
    )
    args = parser.parse_args()
    if not 1 <= args.pct_off <= 100:
        parser.error("pct_off must be between 1 and 100")
    if args.max_uses < 1 or args.per_user_max_uses < 1:
        parser.error("coupon limits must be at least 1")
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.timeout < 0:
        parser.error("--timeout must be 0 or greater")

    expires_at = datetime.now(timezone.utc) + timedelta(days=args.days)
    with Shop() as shop:
        result = shop.coupons.create(
            args.code,
            args.pct_off,
            max_uses=args.max_uses,
            per_user_max_uses=args.per_user_max_uses,
            expires_at=expires_at,
        )
        if isinstance(result, Completed):
            expiry = result.result.expires_at
            expiry_text = expiry.strftime("%Y-%m-%d") if expiry is not None else "no expiry"
            print(
                f"Created coupon {result.result.code}: {result.result.pct_off}% off, "
                f"expires {expiry_text}."
            )
            return 0
        if isinstance(result, PendingApproval):
            print("This large coupon must be approved by the owner in Telegram.")
            try:
                approval = result.wait(timeout=args.timeout)
            except ApprovalTimeoutError:
                print("The coupon is still waiting for approval.")
                return 1
            print(f"Approval finished with status: {approval.status}")
            return 0 if approval.status == "done" else 1

    print(f"Coupon was not created (status: {result.status}).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
