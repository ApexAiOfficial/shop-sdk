"""A minimal webhook receiver (standard library only).

Run it behind HTTPS on port 443 (a reverse proxy, or your platform's TLS), set
SHOP_WEBHOOK_SECRET to the ``whsec_...`` secret /webhooks showed you, then add the public URL
of this server in Telegram with /webhooks.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from apexai_shop.webhooks import WebhookVerificationError, verify_webhook

SECRET = os.environ["SHOP_WEBHOOK_SECRET"]
seen_ids: set[str] = set()  # use your database in production: a delivery can arrive twice


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - the http.server API
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            event = verify_webhook(body, dict(self.headers), SECRET)
        except WebhookVerificationError:
            self.send_response(400)
            self.end_headers()
            return
        if event.id not in seen_ids:
            seen_ids.add(event.id)
            if event.type == "order.paid":
                print(f"Order {event.data['order_id']} paid: {event.data['total_stars']} Stars")
            elif event.type == "stock.low":
                print(f"Low stock: {event.data['title']} ({event.data['available_keys']} keys left)")
        self.send_response(204)  # answer fast; do slow work after replying
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Receiver).serve_forever()
