from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SchedulerHints:
    """Optional scheduler nudges. All fields are optional; absence means default behavior.

    Keep minimal for now to avoid policy creep; extend as needed.
    """

    prefer_large_writes: Optional[bool] = None
    snapshot_consistency_only: Optional[bool] = None


