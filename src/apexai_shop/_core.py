"""Transport-independent pieces shared by :class:`Shop` and :class:`AsyncShop`."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional, Tuple, Type, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from ._operations import OPERATIONS
from ._version import __version__
from .errors import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShopError,
)

ENV_BASE_URL = "SHOP_API_URL"
ENV_TOKEN = "SHOP_API_TOKEN"
USER_AGENT = f"apexai-shop-python/{__version__}"

#: Seconds to wait before retry 1, 2, 3, ... after a 502/503/504 or a transport error.
BACKOFF = (0.5, 1.0, 2.0)
#: The longest Retry-After the client will sleep for.
MAX_RETRY_AFTER = 30.0
RETRY_STATUSES = frozenset({502, 503, 504})

M = TypeVar("M", bound=BaseModel)


def resolve_config(base_url: Optional[str], token: Optional[str]) -> Tuple[str, str]:
    """Fill in base URL and token from the environment and validate them."""
    if base_url is None:
        base_url = os.environ.get(ENV_BASE_URL)
    if not base_url or not base_url.strip():
        raise ValueError(f"No API base URL: pass base_url= or set {ENV_BASE_URL}.")
    if token is None:
        token = os.environ.get(ENV_TOKEN)
    if not token or not token.strip():
        raise ValueError(f"No API token: pass token= or set {ENV_TOKEN}.")
    base_url = base_url.strip()
    if not base_url.lower().startswith(("https://", "http://")):
        raise ValueError("The API base URL must start with https:// or http://.")
    return base_url.rstrip("/"), token.strip()


def default_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def _encode(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def build_request(
    base_url: str,
    operation_id: str,
    path_params: Optional[Mapping[str, Any]] = None,
    query: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Return ``(method, url, params)`` for one operation.

    ``base_url`` has no trailing slash (see :func:`resolve_config`), so the
    operation path is appended to it and the ``/api/merchant/v1`` prefix is
    always kept. ``None`` query values are omitted.
    """
    method, path, allowed = OPERATIONS[operation_id]
    if path_params:
        path = path.format(
            **{name: quote(str(value), safe="") for name, value in path_params.items()}
        )
    params: Dict[str, Any] = {}
    for name, value in (query or {}).items():
        if name not in allowed:
            raise TypeError(f"{operation_id} has no query parameter {name!r}")
        if value is not None:
            params[name] = _encode(value)
    return method, base_url + path, params


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds from a Retry-After header (delta-seconds or HTTP date), or None."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if seconds != seconds:  # NaN
        return None
    return max(0.0, seconds)


def backoff(attempt: int) -> float:
    return BACKOFF[min(attempt, len(BACKOFF) - 1)]


def retry_delay(response: httpx.Response, attempt: int) -> Optional[float]:
    """How long to wait before retrying ``response``, or None if it is final."""
    status = response.status_code
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None:
            return backoff(attempt)
        return min(retry_after, MAX_RETRY_AFTER)
    if status in RETRY_STATUSES:
        return backoff(attempt)
    return None


_DEFAULT_MESSAGES = {
    400: "The server refused a parameter.",
    401: "No token, or a token that is unknown, revoked or expired.",
    403: "The token lacks the scope this operation needs.",
    404: "Not found.",
    422: "A parameter does not match the contract.",
    429: "Too many requests; wait for Retry-After seconds.",
}


def error_from_response(response: httpx.Response) -> ShopError:
    status = response.status_code
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    code = body.get("error")
    if not isinstance(code, str) or not code:
        code = f"http_{status}"
    message = body.get("message")
    if not isinstance(message, str) or not message:
        if status >= 500:
            message = "The server failed to handle the request."
        else:
            message = _DEFAULT_MESSAGES.get(status, f"Unexpected HTTP status {status}.")
    request_id = response.headers.get("x-request-id")

    if status in (400, 422):
        fields = body.get("fields")
        if not isinstance(fields, list):
            fields = []
        return BadRequestError(
            status, code, message, request_id, fields=[str(f) for f in fields]
        )
    if status == 401:
        return AuthenticationError(status, code, message, request_id)
    if status == 403:
        return PermissionDeniedError(status, code, message, request_id)
    if status == 404:
        return NotFoundError(status, code, message, request_id)
    if status == 429:
        return RateLimitError(
            status,
            code,
            message,
            request_id,
            retry_after=parse_retry_after(response.headers.get("retry-after")),
        )
    if status >= 500:
        return ServerError(status, code, message, request_id)
    return ShopError(status, code, message, request_id)


def parse_response(response: httpx.Response, model: Type[M]) -> M:
    request_id = response.headers.get("x-request-id")
    try:
        payload = response.json()
    except ValueError:
        raise ShopError(
            response.status_code,
            "invalid_response",
            "The API returned a body that is not JSON.",
            request_id,
        ) from None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ShopError(
            response.status_code,
            "invalid_response",
            f"The API response does not match {model.__name__} "
            f"({exc.error_count()} problem(s)).",
            request_id,
        ) from exc


def connection_message(exc: Exception) -> str:
    detail = str(exc)
    name = type(exc).__name__
    return f"Could not reach the API ({name}: {detail})" if detail else (
        f"Could not reach the API ({name})"
    )
