from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Set, Dict, Any
from .workspace import Snapshot


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
