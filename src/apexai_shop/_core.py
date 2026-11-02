"""Transport-independent pieces shared by :class:`Shop` and :class:`AsyncShop`."""

from __future__ import annotations

import os
import re
import secrets
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type, TypeVar, Union
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from ._operations import OPERATIONS
from ._version import __version__
from .errors import (
    ApprovalRequiredError,
    AuthenticationError,
    BadRequestError,
    ChangedError,
    ConfirmationInvalidError,
    ConfirmKeyDeliveryError,
    ConflictError,
    DailyCapError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShopError,
    StoreSuspendedError,
    WritesDisabledError,
)
from .models import ConfirmationInfo, WriteAnswer
from .results import Completed, Confirm, ConfirmationRequired, PendingApproval, WriteResult

ENV_BASE_URL = "SHOP_API_URL"
ENV_TOKEN = "SHOP_API_TOKEN"
USER_AGENT = f"apexai-shop-python/{__version__}"

#: Seconds to wait before retry 1, 2, 3, ... after a 502/503/504 or a transport error.
BACKOFF = (0.5, 1.0, 2.0)
#: The longest Retry-After the client will sleep for.
MAX_RETRY_AFTER = 30.0
RETRY_STATUSES = frozenset({502, 503, 504})
#: The header idempotent writes carry, and the pattern its value must match.
IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
#: 409 codes a write retries (with the same key): an identical request is still running.
WRITE_RETRY_CODES_409 = frozenset({"idempotency_in_progress"})
#: 429 codes a write never retries: waiting seconds does not help.
WRITE_FINAL_CODES_429 = frozenset({"daily_cap"})
#: Transport errors raised before a request could reach the server.
NOT_SENT_ERRORS: Tuple[Type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
)
#: Statuses a gateway answers when the request may have reached the server anyway.
GATEWAY_STATUSES = frozenset({502, 504})

M = TypeVar("M", bound=BaseModel)
A = TypeVar("A", bound=WriteAnswer)


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


def _encode_json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def build_request(
    base_url: str,
    operation_id: str,
    path_params: Optional[Mapping[str, Any]] = None,
    query: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Return ``(method, url, params, json_body)`` for one operation.

    ``base_url`` has no trailing slash (see :func:`resolve_config`), so the
    operation path is appended to it and the ``/api/merchant/v1`` prefix is
    always kept. Path parameters are percent-encoded. ``None`` query values
    and ``None`` body fields are omitted (for a write, ``None`` means "leave
    as it is"; the contract tests check that every nullable body field is
    optional, so omitting one equals sending null). A ``confirmation`` may be
    the preview's :class:`ConfirmationInfo` or its id. Reads have no body
    (``json_body`` is ``None``); a write always sends a JSON object, possibly
    empty.
    """
    operation = OPERATIONS[operation_id]
    path = operation.path
    if path_params:
        path = path.format(
            **{name: quote(str(value), safe="") for name, value in path_params.items()}
        )
    params: Dict[str, Any] = {}
    for name, value in (query or {}).items():
        if name not in operation.query:
            raise TypeError(f"{operation_id} has no query parameter {name!r}")
        if value is not None:
            params[name] = _encode(value)
    json_body: Optional[Dict[str, Any]] = None
    if operation.is_write:
        json_body = {}
        for name, value in (body or {}).items():
            if name not in operation.body:
                raise TypeError(f"{operation_id} has no body field {name!r}")
            if isinstance(value, ConfirmationInfo):
                value = value.id
            if value is not None:
                json_body[name] = _encode_json(value)
    elif body:
        raise TypeError(f"{operation_id} takes no body")
    return operation.method, base_url + path, params, json_body


def confirmable(operation_id: str) -> bool:
    """Whether the operation takes a ``confirmation`` (a two-step write)."""
    return "confirmation" in OPERATIONS[operation_id].body


def idempotency_key(key: Optional[str] = None) -> str:
    """The ``Idempotency-Key`` for one logical write: ``key`` checked, or a new random one.

    The same key is sent on every retry of that write, so a retry the server
    already handled replays its first answer instead of changing the store twice.
    """
    if key is None:
        return secrets.token_urlsafe(24)  # 32 characters of A-Z a-z 0-9 - _
    if not isinstance(key, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise ValueError("idempotency_key must be 16 to 64 letters, digits, '-' or '_'.")
    return key


def write_key(operation_id: str, key: Optional[str]) -> Optional[str]:
    """Validate/generate a key exactly when the operation contract declares one."""
    if OPERATIONS[operation_id].uses_idempotency_key:
        return idempotency_key(key)
    if key is not None:
        raise TypeError(f"{operation_id} does not take idempotency_key")
    return None


def request_headers(base: Mapping[str, str], key: Optional[str]) -> Dict[str, str]:
    """``base`` plus the ``Idempotency-Key`` header when ``key`` is set (writes)."""
    headers = dict(base)
    if key is not None:
        headers[IDEMPOTENCY_HEADER] = key
    return headers


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


def may_have_arrived(failure: Union[httpx.RequestError, httpx.Response]) -> bool:
    """Whether a retried attempt may have reached the server although its answer was lost.

    True for a request error after the connection was made (a read timeout, a
    reset, an answer that could not be decoded), a 502 or 504 from a gateway,
    and 409 ``idempotency_in_progress`` (an earlier attempt with the same key
    is still running).
    """
    if isinstance(failure, httpx.RequestError):
        return not isinstance(failure, NOT_SENT_ERRORS)
    if failure.status_code in GATEWAY_STATUSES:
        return True
    return failure.status_code == 409 and _error_code(failure) in WRITE_RETRY_CODES_409


def _error_code(response: httpx.Response) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def retry_delay(response: httpx.Response, attempt: int, *, write: bool = False) -> Optional[float]:
    """How long to wait before retrying ``response``, or None if it is final.

    Reads retry 429 (Retry-After, capped at :data:`MAX_RETRY_AFTER`) and
    502/503/504 (backoff). Idempotent writes repeat the same
    ``Idempotency-Key`` and use :func:`write_retry_delay`.
    """
    if write:
        return write_retry_delay(response, attempt)
    status = response.status_code
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None:
            return backoff(attempt)
        return min(retry_after, MAX_RETRY_AFTER)
    if status in RETRY_STATUSES:
        return backoff(attempt)
    return None


def write_retry_delay(response: httpx.Response, attempt: int) -> Optional[float]:
    """How long to wait before retrying a write's ``response``, or None if it is final.

    An idempotent write is retried with the same ``Idempotency-Key`` on 409
    ``idempotency_in_progress`` (the same request is still running), on 429
    other than ``daily_cap``, and on 502/503/504 (including 503 ``busy``). The
    wait is the server's Retry-After, or the backoff when there is none; if
    the server asks for longer than :data:`MAX_RETRY_AFTER`, the answer is
    final and its error is raised at once.
    """
    status = response.status_code
    if status == 409:
        if _error_code(response) not in WRITE_RETRY_CODES_409:
            return None
    elif status == 429:
        if _error_code(response) in WRITE_FINAL_CODES_429:
            return None
    elif status not in RETRY_STATUSES:
        return None
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    if retry_after is None:
        return backoff(attempt)
    if retry_after > MAX_RETRY_AFTER:
        return None
    return retry_after


_DEFAULT_MESSAGES = {
    400: "The server refused a parameter.",
    401: "No token, or a token that is unknown, revoked or expired.",
    403: "The token lacks the scope this operation needs.",
    404: "Not found.",
    409: "The change conflicts with the store's current state.",
    422: "A parameter does not match the contract.",
    423: "The store is suspended.",
    429: "Too many requests; wait for Retry-After seconds.",
}


def _str_list(value: Any) -> List[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


#: Added to a 409 ``confirmation_invalid`` that follows an attempt whose answer was lost.
LOST_ANSWER_NOTE = (
    "An earlier attempt of this call got no answer: it may have found that the effect "
    "changed, which spends the confirmation (had it made the change, the API would have "
    "answered the stored result). This call changed nothing: call the operation again "
    "without a confirmation for a new preview, and show the user its effect."
)


def error_from_response(
    response: httpx.Response,
    bind_preview: Optional[Callable[[Dict[str, Any]], Optional[ConfirmationRequired[Any]]]] = None,
    *,
    answer_lost: bool = False,
) -> ShopError:
    """The :class:`ShopError` subclass for an error ``response``.

    ``bind_preview`` turns the fresh preview of a 409 ``changed`` into a
    :class:`ConfirmationRequired` bound to the write that got it.
    ``answer_lost``: an earlier attempt of the same call may have reached the
    server (see :func:`may_have_arrived`), which explains a
    ``confirmation_invalid`` on a retry.
    """
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
        return BadRequestError(
            status, code, message, request_id, fields=_str_list(body.get("fields"))
        )
    if status == 401:
        return AuthenticationError(status, code, message, request_id)
    if status == 403:
        if code == "approval_required":
            return ApprovalRequiredError(
                status, code, message, request_id, reasons=_str_list(body.get("reasons"))
            )
        if code == "writes_disabled":
            return WritesDisabledError(status, code, message, request_id)
        return PermissionDeniedError(status, code, message, request_id)
    if status == 404:
        return NotFoundError(status, code, message, request_id)
    if status == 409:
        if code == "changed":
            preview = body.get("preview")
            bound = None
            if isinstance(preview, dict) and bind_preview is not None:
                bound = bind_preview(preview)
            return ChangedError(status, code, message, request_id, preview=bound)
        if code == "confirmation_invalid":
            if answer_lost:
                message = f"{message} {LOST_ANSWER_NOTE}"
            return ConfirmationInvalidError(status, code, message, request_id)
        if code == "confirm_key_delivery":
            message = (
                f"{message} Pass enable_key_delivery=True only after the merchant "
                "agrees to turn on key delivery permanently."
            )
            return ConfirmKeyDeliveryError(status, code, message, request_id)
        current = body.get("current")
        return ConflictError(
            status, code, message, request_id, current=current if isinstance(current, dict) else None
        )
    if status == 423:
        return StoreSuspendedError(status, code, message, request_id)
    if status == 429:
        cls = DailyCapError if code == "daily_cap" else RateLimitError
        return cls(
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


_RESULT_CLASSES = {
    "done": Completed,
    "confirmation_required": ConfirmationRequired,
    "pending_approval": PendingApproval,
}


def _types_result(answer: WriteAnswer) -> bool:
    """Whether the operation's answer model types ``result`` (so ``done`` carries one)."""
    field = type(answer).model_fields.get("result")
    return field is not None and field.annotation not in (None, type(None))


def write_result(
    answer: WriteAnswer,
    *,
    confirm: Optional[Confirm] = None,
    status: Optional[int] = None,
    request_id: Optional[str] = None,
    replayed: bool = False,
    preview_class: Type[ConfirmationRequired[Any]] = ConfirmationRequired,
    pending_class: Type[PendingApproval[Any]] = PendingApproval,
    approval_get: Optional[Callable[[int], Any]] = None,
    sleep: Optional[Callable[[float], Any]] = None,
) -> WriteResult[Any]:
    """Wrap a parsed envelope in :class:`Completed`, :class:`ConfirmationRequired`
    (``preview_class``) or :class:`PendingApproval` (a plain :class:`WriteResult`
    for a status this client does not know). ``status`` and ``request_id`` only
    label an error.

    A preview without its ``confirmation`` or ``effect``, an approval without
    ``approval``, and ``done`` without the ``result`` its operation returns are
    ``invalid_response`` errors, so those fields are never ``None`` on the
    typed results.
    """
    cls: Type[WriteResult[Any]] = _RESULT_CLASSES.get(answer.status, WriteResult)
    if cls is PendingApproval:
        cls = pending_class
    missing = None
    if cls is ConfirmationRequired:
        cls = preview_class
        if answer.confirmation is None:
            missing = "confirmation"
        elif answer.effect is None:
            missing = "effect"
    elif issubclass(cls, PendingApproval) and answer.approval is None:
        missing = "approval"
    elif cls is Completed and answer.result is None and _types_result(answer):
        raise ShopError(
            status,
            "invalid_response",
            "The API answered done without result: the change was made, but this client "
            "cannot show it.",
            request_id,
        )
    if missing is not None:
        raise ShopError(
            status,
            "invalid_response",
            f"The API answered {answer.status} without {missing}.",
            request_id,
        )
    return cls(
        answer,
        request_id=request_id,
        replayed=replayed,
        confirm=confirm,
        approval_get=approval_get,
        sleep=sleep,
    )


def parse_write(
    response: httpx.Response,
    model: Type[A],
    confirm: Optional[Confirm] = None,
    *,
    preview_class: Type[ConfirmationRequired[Any]] = ConfirmationRequired,
    pending_class: Type[PendingApproval[Any]] = PendingApproval,
    approval_get: Optional[Callable[[int], Any]] = None,
    sleep: Optional[Callable[[float], Any]] = None,
) -> WriteResult[Any]:
    """A write's successful (200 or 202) ``response`` as a typed result."""
    answer = parse_response(response, model)
    replayed = response.headers.get("idempotent-replayed", "").strip().lower() == "true"
    return write_result(
        answer,
        confirm=confirm,
        status=response.status_code,
        request_id=response.headers.get("x-request-id"),
        replayed=replayed,
        preview_class=preview_class,
        pending_class=pending_class,
        approval_get=approval_get,
        sleep=sleep,
    )


def preview_binder(
    model: Type[A],
    confirm: Confirm,
    *,
    preview_class: Type[ConfirmationRequired[Any]] = ConfirmationRequired,
) -> Callable[[Dict[str, Any]], Optional[ConfirmationRequired[Any]]]:
    """For :func:`error_from_response`: a 409 ``changed`` preview as a bound
    :class:`ConfirmationRequired`, or ``None`` if it is not one."""

    def bind(preview: Dict[str, Any]) -> Optional[ConfirmationRequired[Any]]:
        try:
            answer = model.model_validate(preview)
            result = write_result(answer, confirm=confirm, preview_class=preview_class)
        except (ValidationError, ShopError):
            return None
        return result if isinstance(result, ConfirmationRequired) else None

    return bind


def not_confirmable() -> ShopError:
    """What ``confirm()`` raises on a preview from an operation that takes no confirmation."""
    return ShopError(
        None,
        "invalid_response",
        "The API asked to confirm a change this client cannot confirm: the operation takes no "
        "confirmation in this contract version. Nothing was changed.",
    )


def connection_message(exc: Exception) -> str:
    detail = str(exc)
    name = type(exc).__name__
    return f"Could not reach the API ({name}: {detail})" if detail else (
        f"Could not reach the API ({name})"
    )
