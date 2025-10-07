"""Utility for surgically updating test files without full rewrites."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class Replacement:
    """Single replacement to apply to a test file."""

    before: str
    after: str
    count: int = 1


@dataclass(frozen=True)
class FixResult:
    """Outcome of applying precise fixes."""

    changed: bool
    content: str
    applied: List[str]


class PreciseTestFixer:
    """Apply high-signal edits to an existing test file."""

    def __init__(self, repo_root: str | Path = ".") -> None:
        self.repo_root = Path(repo_root).resolve()

    def apply(self, relative_path: str | Path, replacements: Sequence[Replacement]) -> FixResult:
        """Apply replacements to ``relative_path`` and return new content.

        The method never writes to disk – callers can decide whether to persist
        the edits. Each replacement uses ``str.replace`` with ``count`` to ensure
        we only modify intended occurrences. When no replacements succeed the
        original content is returned.
        """

        target = self.repo_root / Path(relative_path)
        if not target.exists():
            raise FileNotFoundError(target)

        original = target.read_text(encoding="utf-8")
        content = original
        applied: List[str] = []

        for replacement in replacements:
            new_content, edits = _apply_replacement(content, replacement)
            if edits:
                applied.append(edits)
                content = new_content

        changed = content != original
        return FixResult(changed=changed, content=content, applied=applied)

    @staticmethod
    def preview(original: str, updated: str) -> str:
        """Return a unified diff preview between ``original`` and ``updated``."""

        diff = difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
        return "\n".join(diff)


def _apply_replacement(text: str, replacement: Replacement) -> tuple[str, Optional[str]]:
    """Apply a single replacement to ``text`` returning new text and log."""

    new_text, occurrences = _replace_limited(text, replacement.before, replacement.after, replacement.count)
    if occurrences == 0:
        return text, None
    summary = f"Replaced {occurrences} occurrence(s) of {replacement.before!r}"
    return new_text, summary


def _replace_limited(text: str, before: str, after: str, count: int) -> tuple[str, int]:
    if not before:
        return text, 0
    new_text = text.replace(before, after, count if count >= 0 else text.count(before))
    occurrences = (text.count(before) if count < 0 else min(count, text.count(before)))
    return new_text, occurrences
