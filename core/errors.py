from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Set, Dict, Any
from .workspace import Snapshot


class GoalBlocked(Exception):
    """Raised by goals to indicate a terminal failure condition."""

    def __init__(self, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


@dataclass(frozen=True)
class WhyNot:
    reason: str                 # "no_ready", "verify_failed", "conflict", "budget", "no_progress"
    missing: Optional[Set[str]] = None
    details: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class SolveResult:
    ok: bool
    final: Snapshot
    blocker: Optional[WhyNot] = None
    steps: int = 0
