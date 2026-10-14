from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Set, Dict, Any
from .workspace import Snapshot
from .contracts import ContractViolation, FieldContractViolation


@dataclass(frozen=True)
class GoalCheck:
    """Optional goal result with named requirements that remain unmet."""
    met: bool
    unmet: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.met) is not bool:
            raise TypeError('GoalCheck.met must be a bool')
        if isinstance(self.unmet, str):
            raise TypeError('GoalCheck.unmet must contain non-empty strings')
        unmet = tuple(self.unmet)
        if not all(isinstance(item, str) and item for item in unmet):
            raise TypeError('GoalCheck.unmet must contain non-empty strings')
        object.__setattr__(self, 'unmet', unmet)
        if self.met and self.unmet:
            raise ValueError('A met goal cannot have unmet requirements')

    def __bool__(self):
        return self.met


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
    run_id: Optional[str] = None
