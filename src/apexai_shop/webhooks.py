"""Verify the signed webhooks a Shop Bot store sends to your server.

A store owner adds your HTTPS URL in Telegram with ``/webhooks`` and gets a signing secret
(``whsec_...``) once. Every request then carries three headers:

- ``Shop-Webhook-Id``: the event id (use it to ignore a repeated delivery);
- ``Shop-Webhook-Timestamp``: Unix seconds when the request was signed;
- ``Shop-Webhook-Signature``: ``v1=<hex HMAC-SHA256(secret, f"{timestamp}.{body}")>``.

>>> from apexai_shop.webhooks import verify_webhook
>>> event = verify_webhook(request_body, request_headers, secret="whsec_...")  # doctest: +SKIP
>>> if event.type == "order.paid":                                              # doctest: +SKIP
...     print(event.data["order_id"])

Pass the raw request body exactly as received: parsing and re-serializing JSON changes the
bytes and breaks the signature. Answer with a 2xx status quickly; the store retries failed
deliveries with backoff for 24 hours, so your handler must tolerate the same event twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "ID_HEADER",
    "WebhookEvent",
    "WebhookVerificationError",
    "compute_signature",
    "verify_webhook",
]

ID_HEADER = "Shop-Webhook-Id"
TIMESTAMP_HEADER = "Shop-Webhook-Timestamp"
SIGNATURE_HEADER = "Shop-Webhook-Signature"
DEFAULT_TOLERANCE_SECONDS = 300
_SCHEME = "v1"


class WebhookVerificationError(Exception):
    """The request is not a genuine, fresh webhook from the store: reject it (for example 400)."""


class WebhookEvent(BaseModel):
    """A verified event. ``data`` follows the event's schema in the published contract."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    created_at: datetime
    api_revision: str
    data: Dict[str, Any]


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Header lookup that ignores case, for plain dicts as well as framework header objects."""
    value = headers.get(name)
    if value is not None:
        return value
    wanted = name.lower()
    for key, item in headers.items():
        if str(key).lower() == wanted:
            return item
    return None


def compute_signature(secret: str, timestamp: Union[int, str], body: Union[bytes, str]) -> str:
    """The ``v1=...`` value a request with this timestamp and body carries."""
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    message = str(timestamp).encode("ascii") + b"." + raw
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{_SCHEME}={digest}"


def verify_webhook(
    body: Union[bytes, str],
    headers: Mapping[str, str],
    secret: str,
    *,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> WebhookEvent:
    """Check the signature and the timestamp, then return the event.

    Raises :class:`WebhookVerificationError` when a header is missing, the timestamp is more
    than ``tolerance`` seconds away from ``now`` (a replay or a skewed clock), no ``v1``
    signature matches (compared in constant time), or the body is not an event.
    """
    if not secret:
        raise WebhookVerificationError("No signing secret was given.")
    timestamp = _header(headers, TIMESTAMP_HEADER)
    signature = _header(headers, SIGNATURE_HEADER)
    if not timestamp or not signature:
        raise WebhookVerificationError("The webhook headers are missing.")
    try:
        sent_at = int(timestamp.strip())
    except ValueError:
        raise WebhookVerificationError("The webhook timestamp is not a number.") from None
    current = time.time() if now is None else now
    if abs(current - sent_at) > tolerance:
        raise WebhookVerificationError("The webhook timestamp is outside the tolerance.")
    expected = compute_signature(secret, timestamp.strip(), body)
    # Several values may be sent one day (for example during a secret rotation): any match passes.
    candidates = [part.strip() for part in signature.replace(" ", ",").split(",") if part.strip()]
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates if candidate.startswith(_SCHEME + "=")):
        raise WebhookVerificationError("The webhook signature does not match.")
    try:
        parsed = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
        return WebhookEvent.model_validate(parsed)
    except (ValueError, TypeError) as exc:
        raise WebhookVerificationError("The webhook body is not a valid event.") from exc
