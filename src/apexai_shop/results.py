"""What a write returns: :class:`Completed`, :class:`ConfirmationRequired` or :class:`PendingApproval`.

Every write answers the same envelope (the contract's ``WriteAnswer``). The
client wraps it in one of three classes by its ``status``; each keeps the
envelope's field names (``status``, ``replayed``, ``noop``, ``result``,
``confirmation``, ``approval``, ``effect``, ``next``) and adds the
``request_id`` the server sent. A status newer than this client comes back
as a plain :class:`WriteResult`::

    answer = shop.categories.delete(7)
    if isinstance(answer, ConfirmationRequired):
        print(answer.effect.summary)          # show the user what will happen
        if user_agrees():
            answer = answer.confirm()         # the same call, plus the confirmation id
    if isinstance(answer, Completed):
        print("deleted", answer.result.category_id)

With :class:`~apexai_shop.AsyncShop` a preview is an
:class:`AsyncConfirmationRequired` (also a :class:`ConfirmationRequired`)
whose ``confirm()`` is awaited.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

from .models import ApprovalInfo, ConfirmationInfo, Effect, WriteAnswer

__all__ = [
    "WriteResult",
    "Completed",
    "ConfirmationRequired",
    "AsyncConfirmationRequired",
    "PendingApproval",
]

R = TypeVar("R")

#: Repeats a write with ``confirmation`` added: ``(confirmation, idempotency_key) -> result``.
#: For :class:`~apexai_shop.AsyncShop` it returns an awaitable.
Confirm = Callable[[str, Optional[str]], Any]
UNBOUND = "This preview is not bound to a client; pass its confirmation id yourself."


class WriteResult(Generic[R]):
    """Base class of every write's answer; the fields mirror the API envelope.

    ``answer`` is the parsed envelope itself (a pydantic model), for logging
    or returning it as data: ``result.answer.model_dump(mode="json")``. Unlike
    ``repr()``, that dump includes a preview's single-use confirmation id.
    """

    status: str
    """``done``, ``confirmation_required`` or ``pending_approval``."""
    replayed: bool
    """True when this is the stored answer of an earlier request: a retry with
    the same ``Idempotency-Key`` and body, or a confirmation sent again after
    it made its change. The change was made once, before."""
    noop: bool
    """True when nothing needed to change (the store already looked like that)."""
    result: Optional[R]
    """The changed object, for ``done``; ``None`` otherwise."""
    confirmation: Optional[ConfirmationInfo]
    approval: Optional[ApprovalInfo]
    effect: Optional[Effect]
    """What the change does (``summary`` for people, ``details`` for code)."""
    next: Optional[str]
    """One sentence for an agent: what to do next."""
    request_id: Optional[str]
    answer: WriteAnswer
    """The envelope as the API sent it."""

    def __init__(
        self,
        answer: WriteAnswer,
        *,
        request_id: Optional[str] = None,
        replayed: bool = False,
        confirm: Optional[Confirm] = None,
    ) -> None:
        self.answer = answer
        self.status = answer.status
        self.replayed = bool(answer.replayed or replayed)
        self.noop = answer.noop
        self.result = answer.result
        self.confirmation = answer.confirmation
        self.approval = answer.approval
        self.effect = answer.effect
        self.next = answer.next
        self.request_id = request_id
        self._confirm = confirm

    def __repr__(self) -> str:
        parts = [f"status={self.status!r}"]
        if self.replayed:
            parts.append("replayed=True")
        if self.noop:
            parts.append("noop=True")
        if self.result is not None:
            parts.append(f"result={self.result!r}")
        if self.confirmation is not None:
            parts.append(f"confirmation_expires_at={self.confirmation.expires_at.isoformat()!r}")
        if self.approval is not None:
            parts.append(f"approval={self.approval!r}")
        if self.effect is not None:
            parts.append(f"effect={self.effect.summary!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


class Completed(WriteResult[R]):
    """``done`` (HTTP 200): the change is made.

    ``result`` is the changed object (the client checks that the API sent
    one). ``noop`` is true when nothing needed to change, and ``replayed``
    when this is the stored answer of an earlier request, so the change was
    not made twice.
    """

    result: R


class ConfirmationRequired(WriteResult[R]):
    """``confirmation_required`` (HTTP 202): a preview; nothing has changed yet.

    Show the user ``effect`` and call :meth:`confirm` only after they agree,
    before ``confirmation.expires_at``. A confirmation is single use and only
    valid for the same operation and arguments.

    :meth:`confirm` sends the call through the client that made the preview,
    so that client must still be open. To confirm later or from another
    process, pass ``confirmation=`` the preview's ``confirmation`` (or its
    ``id``) to the same method with the same arguments.
    """

    confirmation: ConfirmationInfo
    effect: Effect

    def confirm(self, *, idempotency_key: Optional[str] = None) -> WriteResult[R]:
        """Repeat the same operation with the same arguments plus ``confirmation``.

        Returns the new answer, normally :class:`Completed`. The confirming
        call gets its own ``Idempotency-Key`` (pass ``idempotency_key=`` to
        choose it). If it fails without an answer (``ConnectionError``, for
        example), calling ``confirm()`` again is safe: a confirmation that
        already made its change answers the stored result (``replayed``).
        """
        if self._confirm is None:
            raise TypeError(UNBOUND)
        result: WriteResult[R] = self._confirm(self.confirmation.id, idempotency_key)
        return result


class AsyncConfirmationRequired(ConfirmationRequired[R]):
    """A preview from :class:`~apexai_shop.AsyncShop`: ``await preview.confirm()``.

    It is a :class:`ConfirmationRequired` too; check for this class to have a
    type checker know that ``confirm()`` returns an awaitable.
    """

    def confirm(  # type: ignore[override]
        self, *, idempotency_key: Optional[str] = None
    ) -> Awaitable[WriteResult[R]]:
        """Repeat the same operation with ``confirmation`` added; await the new answer."""
        if self._confirm is None:
            raise TypeError(UNBOUND)
        result: Awaitable[WriteResult[R]] = self._confirm(self.confirmation.id, idempotency_key)
        return result


class PendingApproval(WriteResult[R]):
    """``pending_approval`` (HTTP 202): the change waits for the store owner's Approve in Telegram.

    Nothing has changed yet and there is nothing to send again: tell the user
    the owner must approve, and never report the change as made. ``approval``
    holds the approval's ``id``, ``status`` and ``expires_at``. (A 403
    ``approval_required`` error is different: the API refused the change and
    queued nothing; see :class:`~apexai_shop.ApprovalRequiredError`.)
    """

    approval: ApprovalInfo

    # TODO(get_approval): add ``wait(timeout=..., poll=2.0)`` that polls the
    # approval until it is decided, once the API serves a read for approvals
    # (``get_approval``). It is not part of contract version 1 yet.
