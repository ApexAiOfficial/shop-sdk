"""verify_webhook: signatures, freshness, headers and the event model."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from apexai_shop.webhooks import (
    WebhookEvent,
    WebhookVerificationError,
    compute_signature,
    verify_webhook,
)

SECRET = "whsec_test_" + "a" * 32
NOW = 1_762_070_400
BODY = json.dumps(
    {
        "id": "4f7c2a8e-1b2c-4d5e-8f90-123456789abc",
        "type": "order.paid",
        "created_at": "2026-11-02T08:00:00Z",
        "api_revision": "2026-11-02",
        "data": {"order_id": 12, "total_stars": 150},
    },
    separators=(",", ":"),
).encode("utf-8")


def _headers(body: bytes = BODY, *, timestamp: int = NOW, secret: str = SECRET) -> dict:
    return {
        "Shop-Webhook-Id": "4f7c2a8e-1b2c-4d5e-8f90-123456789abc",
        "Shop-Webhook-Timestamp": str(timestamp),
        "Shop-Webhook-Signature": compute_signature(secret, timestamp, body),
    }


def test_the_signature_is_hmac_sha256_over_timestamp_dot_body_keyed_by_the_whole_secret():
    expected = hmac.new(SECRET.encode(), f"{NOW}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, NOW, BODY) == f"v1={expected}"
    assert compute_signature(SECRET, str(NOW), BODY.decode()) == f"v1={expected}"


def test_a_genuine_fresh_event_is_returned():
    event = verify_webhook(BODY, _headers(), SECRET, now=NOW + 10)
    assert isinstance(event, WebhookEvent)
    assert event.type == "order.paid" and event.data["order_id"] == 12
    assert event.api_revision == "2026-11-02"


def test_headers_are_matched_without_regard_to_case_and_str_bodies_work():
    lowered = {key.lower(): value for key, value in _headers().items()}
    assert verify_webhook(BODY.decode(), lowered, SECRET, now=NOW).id.startswith("4f7c2a8e")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h, b: (h, b.replace(b"150", b"999")),  # a tampered body
        lambda h, b: ({**h, "Shop-Webhook-Signature": compute_signature("whsec_other", NOW, b)}, b),  # another secret
        lambda h, b: ({**h, "Shop-Webhook-Timestamp": str(NOW + 1)}, b),  # the timestamp is part of the signature
        lambda h, b: ({k: v for k, v in h.items() if k != "Shop-Webhook-Signature"}, b),
        lambda h, b: ({k: v for k, v in h.items() if k != "Shop-Webhook-Timestamp"}, b),
        lambda h, b: ({**h, "Shop-Webhook-Signature": "v0=" + h["Shop-Webhook-Signature"][3:]}, b),  # unknown scheme
    ],
    ids=["tampered", "wrong_secret", "moved_timestamp", "no_signature", "no_timestamp", "unknown_scheme"],
)
def test_anything_not_genuine_is_refused(mutate):
    headers, body = mutate(_headers(), BODY)
    with pytest.raises(WebhookVerificationError):
        verify_webhook(body, headers, SECRET, now=NOW)


@pytest.mark.parametrize("skew", [-301, 301])
def test_a_stale_or_future_timestamp_is_refused(skew):
    with pytest.raises(WebhookVerificationError, match="tolerance"):
        verify_webhook(BODY, _headers(), SECRET, now=NOW + skew)
    assert verify_webhook(BODY, _headers(), SECRET, now=NOW + skew, tolerance=600).type == "order.paid"


def test_any_of_several_v1_signatures_may_match():
    headers = _headers()
    headers["Shop-Webhook-Signature"] = compute_signature("whsec_old", NOW, BODY) + ", " + headers["Shop-Webhook-Signature"]
    assert verify_webhook(BODY, headers, SECRET, now=NOW).type == "order.paid"


def test_a_signed_body_that_is_not_an_event_is_refused():
    body = b'{"hello": "world"}'
    with pytest.raises(WebhookVerificationError, match="not a valid event"):
        verify_webhook(body, _headers(body), SECRET, now=NOW)


def test_a_missing_secret_is_refused():
    with pytest.raises(WebhookVerificationError):
        verify_webhook(BODY, _headers(), "", now=NOW)


def test_unknown_event_fields_are_ignored():
    payload = json.loads(BODY)
    payload["future_field"] = True
    body = json.dumps(payload).encode()
    assert verify_webhook(body, _headers(body), SECRET, now=NOW).type == "order.paid"


def test_the_event_model_covers_the_contract_envelope_and_every_event_type():
    import pathlib

    contract = json.loads((pathlib.Path(__file__).parent.parent / "contract" / "merchant-webhooks-v1.json").read_text())
    defs = contract["$defs"]
    events = {name: schema for name, schema in defs.items() if set(schema.get("properties", {})) >= {"id", "type", "data"}}
    assert set(contract["x-event-types"]) == {"order.paid", "order.refunded", "ticket.created", "stock.low", "webhook.test"}
    for name, schema in events.items():
        assert set(schema["properties"]) == set(WebhookEvent.model_fields), name
