"""The README's write examples are the code CI type-checks (tests/typecheck/readme_writes.py)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPECHECKED = Path(__file__).resolve().parent / "typecheck" / "readme_writes.py"


def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_readme_write_examples_are_type_checked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", _section(readme, "Changing the store"), re.S)
    assert len(blocks) >= 2
    copy = TYPECHECKED.read_text(encoding="utf-8")
    for block in blocks:
        assert block in copy, block
