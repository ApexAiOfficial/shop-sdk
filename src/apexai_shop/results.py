"""What a write returns: :class:`Completed`, :class:`ConfirmationRequired` or :class:`PendingApproval`.

Every write answers the same envelope (the contract's ``WriteAnswer``). The
client wraps it in one of three classes by its ``status``; each keeps the
envelope's field names (``status``, ``replayed``, ``noop``, ``result``,
``confirmation``, ``approval``, ``effect``, ``next``) and adds the
``request_id`` the server sent::

    answer = shop.categories.delete(7)
    if isinstance(answer, ConfirmationRequired):
        print(answer.effect.summary)          # show the user what will happen
        if user_agrees():
            answer = answer.confirm()         # the same call, plus the confirmation id
    assert isinstance(answer, Completed)
"""

from __future__ import annotations

from typing import Any, Callable, Generic, Optional, TypeVar

from .models import ApprovalInfo, ConfirmationInfo, Effect

__all__ = ["WriteResult", "Completed", "ConfirmationRequired", "PendingApproval"]

R = TypeVar("R")

#: Repeats a write with ``confirmation`` added: ``(confirmation, idempotency_key) -> result``.
#: For :class:`~apexai_shop.AsyncShop` it returns an awaitable.
Confirm = Callable[[str, Optional[str]], Any]


class WriteResult(Generic[R]):
    """Base class of every write's answer; the fields mirror the API envelope."""

    status: str
    """``done``, ``confirmation_required`` or ``pending_approval``."""
    replayed: bool
    """True when this is the stored answer of an earlier identical request
    (same ``Idempotency-Key`` and body): the change was made once, before."""
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

    def __init__(
        self,
        answer: Any,
        *,
        request_id: Optional[str] = None,
        replayed: bool = False,
        confirm: Optional[Confirm] = None,
    ) -> None:
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

    ``result`` is the changed object. ``noop`` is true when nothing needed to
    change, and ``replayed`` when this is the stored answer of an earlier
    identical request, so the change was not made twice.
    """


class ConfirmationRequired(WriteResult[R]):
    """``confirmation_required`` (HTTP 202): a preview; nothing has changed yet.

    Show the user ``effect`` and call :meth:`confirm` only after they agree,
    before ``confirmation.expires_at``. A confirmation is single use and only
    valid for the same operation and arguments.
    """

    confirmation: ConfirmationInfo

    def confirm(self, *, idempotency_key: Optional[str] = None) -> Any:
        """Repeat the same operation with the same arguments plus ``confirmation``.

        Returns the new answer, normally :class:`Completed`. With
        :class:`~apexai_shop.AsyncShop` this is a coroutine: ``await preview.confirm()``.
        The confirming call gets its own ``Idempotency-Key`` (pass
        ``idempotency_key=`` to choose it).
        """
        if self._confirm is None:
            raise TypeError("This preview is not bound to a client; pass its confirmation id yourself.")
        return self._confirm(self.confirmation.id, idempotency_key)


class PendingApproval(WriteResult[R]):
    """``pending_approval`` (HTTP 202): waiting for the store owner to tap Approve in Telegram.

    Nothing has changed yet. Tell the user and never report the change as
    made. ``approval`` holds the approval's ``id``, ``status`` and ``expires_at``.
    """

    approval: ApprovalInfo

    # TODO(get_approval): add ``wait(timeout=..., poll=2.0)`` that polls the
    # approval until it is decided, once the API serves a read for approvals
    # (``get_approval``). It is not part of contract version 1 yet.
