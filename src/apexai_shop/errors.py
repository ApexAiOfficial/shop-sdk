"""Exceptions raised by the client.

Every error the API returns becomes a :class:`ShopError` subclass carrying the
HTTP status, the stable error code from the response body (for example
``unauthorized``, ``insufficient_scope`` or ``not_found``), a readable message
and the request id when the server sent one. Errors never contain the token.

Refused writes add a few classes keyed on their stable codes:
:class:`ConflictError` (409, with :class:`ChangedError` and
:class:`ConfirmationInvalidError`), :class:`StoreSuspendedError` (423),
:class:`DailyCapError` (429 ``daily_cap``), :class:`ApprovalRequiredError` and
:class:`WritesDisabledError` (403).

Every error a write raises carries that call's ``idempotency_key``. A
:class:`ConnectionError`, a :class:`ServerError` or an ``invalid_response``
does not say whether the change was made: repeat the same call with
``idempotency_key=err.idempotency_key`` and the API answers the stored result
if it was, or makes the change once if it was not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .results import ConfirmationRequired

__all__ = [
    "ShopError",
    "AuthenticationError",
    "PermissionDeniedError",
    "ApprovalRequiredError",
    "WritesDisabledError",
    "NotFoundError",
    "BadRequestError",
    "ConflictError",
    "ChangedError",
    "ConfirmationInvalidError",
    "StoreSuspendedError",
    "RateLimitError",
    "DailyCapError",
    "ServerError",
    "ConnectionError",
]


class ShopError(Exception):
    """Base class for every error raised by the client.

    ``idempotency_key`` is the ``Idempotency-Key`` the failed write sent
    (``None`` for reads): pass it to the same call to retry it safely.
    """

    idempotency_key: Optional[str] = None

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(self.__str__())

    def __str__(self) -> str:
        head = f"{self.status} {self.code}" if self.status is not None else self.code
        text = f"{head}: {self.message}"
        if self.request_id:
            text += f" (request id {self.request_id})"
        return text

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status={self.status!r}, code={self.code!r}, "
            f"message={self.message!r}, request_id={self.request_id!r})"
        )


class AuthenticationError(ShopError):
    """401: no token, or a token that is unknown, revoked or expired."""


class PermissionDeniedError(ShopError):
    """403: the token lacks the scope this operation needs (``insufficient_scope``).

    :class:`ApprovalRequiredError` and :class:`WritesDisabledError` are the
    other 403 answers a write can get.
    """


class ApprovalRequiredError(PermissionDeniedError):
    """403 ``approval_required``: refused, because this change needs the store owner's approval.

    Nothing was changed and nothing was queued. ``reasons`` lists why, in the
    server's words: ask the store owner to make the change in the bot. (A
    :class:`~apexai_shop.PendingApproval` result is different: the change was
    queued and waits for the owner to tap Approve in Telegram.)
    """

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        reasons: Optional[List[str]] = None,
    ) -> None:
        self.reasons: List[str] = list(reasons or [])
        super().__init__(status, code, message, request_id)

    def __str__(self) -> str:
        text = super().__str__()
        if self.reasons:
            text += f" [reasons: {'; '.join(self.reasons)}]"
        return text


class WritesDisabledError(PermissionDeniedError):
    """403 ``writes_disabled``: changes through the API are switched off for this store."""


class NotFoundError(ShopError):
    """404: no such item or order."""


class BadRequestError(ShopError):
    """400 or 422: a parameter the server refused.

    ``fields`` names the offending parameters (never their values) when the
    server reports them, as it does for 422 responses.
    """

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> None:
        self.fields: List[str] = list(fields or [])
        super().__init__(status, code, message, request_id)

    def __str__(self) -> str:
        text = super().__str__()
        if self.fields:
            text += f" [fields: {', '.join(self.fields)}]"
        return text


class ConflictError(ShopError):
    """409: the change conflicts with the store's current state.

    For code ``conflict`` (an edit based on an outdated version), ``current``
    holds the object as it is now, so you can show it and try again. Other
    409 codes (``idempotency_in_progress`` once the client's retries are
    spent, and codes newer than this client) raise this class too, with
    ``current`` set to ``None``.
    """

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        current: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.current = current
        super().__init__(status, code, message, request_id)


class ChangedError(ConflictError):
    """409 ``changed``: the effect changed between the preview and the confirmation.

    Nothing was changed and the old confirmation is spent. ``preview`` is a
    fresh :class:`~apexai_shop.ConfirmationRequired` for the same operation
    and arguments: show the user its new ``effect`` and call
    ``preview.confirm()`` only if they agree again.
    """

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        preview: "Optional[ConfirmationRequired[Any]]" = None,
    ) -> None:
        self.preview = preview
        super().__init__(status, code, message, request_id)


class ConfirmationInvalidError(ConflictError):
    """409 ``confirmation_invalid``: the confirmation is unknown, expired, used, or for other arguments.

    This call changed nothing. Call the operation again without a
    confirmation for a new preview, and show the user its effect.
    """


class StoreSuspendedError(ShopError):
    """423 ``store_suspended``: the store is suspended, so this change is refused."""


class RateLimitError(ShopError):
    """429: too many requests. ``retry_after`` is in seconds, when known."""

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(status, code, message, request_id)


class DailyCapError(RateLimitError):
    """429 ``daily_cap``: the token spent a rolling 24-hour allowance of changes.

    Never retried automatically. ``retry_after`` is the number of seconds
    until the allowance has room again, when the server says.
    """


class ServerError(ShopError):
    """5xx: the server failed to handle the request."""


class ConnectionError(ShopError):  # noqa: A001 - deliberate, namespaced by the module
    """The API could not be reached (DNS, connect, TLS, timeout, reset)."""

    def __init__(self, message: str) -> None:
        super().__init__(None, "connection_error", message)
