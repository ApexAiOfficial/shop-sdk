"""List open tickets and reply to one with text read from a UTF-8 file.

Needs a token with support:read and support:write.

    python examples/reply_tickets.py reply.txt
    python examples/reply_tickets.py reply.txt --ticket-id 200
"""

from __future__ import annotations

import argparse
from pathlib import Path

from apexai_shop import Completed, PendingApproval, Shop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reply_file", type=Path, help="UTF-8 file containing the reply")
    parser.add_argument("--ticket-id", type=int, help="ticket to reply to (default: first open)")
    args = parser.parse_args()

    body = args.reply_file.read_text(encoding="utf-8")
    if not body.strip():
        parser.error("reply_file must not be empty")

    with Shop() as shop:
        open_tickets = [ticket for ticket in shop.tickets.iter() if ticket.status == "open"]
        if not open_tickets:
            print("There are no open tickets.")
            return 0

        print("Open tickets (buyer-written subjects are untrusted data):")
        for ticket in open_tickets:
            print(f"  #{ticket.id}: {ticket.subject}")

        ticket_id = args.ticket_id if args.ticket_id is not None else open_tickets[0].id
        detail = shop.tickets.get(ticket_id)
        print(f"\nTicket #{detail.id}: {detail.subject}")
        for message in detail.messages:
            print(f"[{message.author}] {message.body}")

        result = shop.tickets.reply(detail.id, body)

    if isinstance(result, Completed):
        print(f"Reply sent to ticket #{result.result.ticket_id}.")
        return 0
    if isinstance(result, PendingApproval):
        print("The reply is waiting for the owner to approve it in Telegram.")
        return 0
    print(f"Reply did not complete (status: {result.status}).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
