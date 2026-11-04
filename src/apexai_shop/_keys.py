"""Secret-safe input handling for licence-key imports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple, Union

from .errors import ShopError

MAX_KEYS_PER_BATCH = 1_000
MAX_KEYS_TEXT_CHARS = 60_000
MAX_JSON_BODY_BYTES = 60 * 1_024


def key_text(*, text: Optional[str], path: Optional[Union[str, os.PathLike[str]]]) -> str:
    """Return exactly one explicit text or UTF-8 file input."""
    if (text is None) == (path is None):
        raise TypeError("Pass exactly one of text= or path=.")
    if text is not None:
        if not isinstance(text, str):
            raise TypeError("text must be a string.")
        return text
    assert path is not None
    return Path(path).read_text(encoding="utf-8")


def _body_size(keys_text: str) -> int:
    body = json.dumps(
        {"keys_text": keys_text}, ensure_ascii=False, separators=(",", ":")
    )
    return len(body.encode("utf-8"))


def batches(keys_text: str) -> Tuple[str, ...]:
    """Split meaningful lines without ever including their text in an error."""
    lines: List[str] = []
    for line in keys_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(line)
    if not lines:
        raise ValueError("The licence-key input contains no key lines.")

    output: List[str] = []
    current: List[str] = []
    for line in lines:
        candidate = "\n".join((*current, line))
        fits = (
            len(current) < MAX_KEYS_PER_BATCH
            and len(candidate) <= MAX_KEYS_TEXT_CHARS
            and _body_size(candidate) < MAX_JSON_BODY_BYTES
        )
        if fits:
            current.append(line)
            continue
        if not current:
            raise ValueError(
                "A licence key is too large for the API's 60 KiB import batch limit."
            )
        output.append("\n".join(current))
        current = [line]
        candidate = line
        if (
            len(candidate) > MAX_KEYS_TEXT_CHARS
            or _body_size(candidate) >= MAX_JSON_BODY_BYTES
        ):
            raise ValueError(
                "A licence key is too large for the API's 60 KiB import batch limit."
            )
    if current:
        output.append("\n".join(current))
    return tuple(output)


def redact_stage_error(exc: ShopError) -> None:
    """Remove an untrusted server message that might echo staged key text."""
    exc.message = "The store refused the licence-key staging request."
    exc.args = (str(exc),)


def add_import_progress(exc: ShopError, batches_committed: int) -> None:
    """Annotate a refusal without changing its useful, specific exception type."""
    exc.batches_committed = batches_committed
    exc.args = (str(exc),)
