"""Exceptions raised by the client.

Every error the API returns becomes a :class:`ShopError` subclass carrying the
HTTP status, the stable error code from the response body (for example
``unauthorized``, ``insufficient_scope`` or ``not_found``), a readable message
and the request id when the server sent one. Errors never contain the token.
"""

from __future__ import annotations

from typing import List, Optional

__all__ = [
    "ShopError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "RateLimitError",
    "ServerError",
    "ConnectionError",
]


class ShopError(Exception):
    """Base class for every error raised by the client."""

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
    """403: the token lacks the scope this operation needs."""


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


class ServerError(ShopError):
    """5xx: the server failed to handle the request."""


class ConnectionError(ShopError):  # noqa: A001 - deliberate, namespaced by the module
    """The API could not be reached (DNS, connect, TLS, timeout, reset)."""

    def __init__(self, message: str) -> None:
        super().__init__(None, "connection_error", message)
