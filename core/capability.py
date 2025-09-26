from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Set, Dict, Any, Optional, Protocol
from .workspace import Snapshot
from .merge import WriteSpec


class Runner(Protocol):
    def run(self, cap: "Capability", snap: Snapshot) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class Capability:
    id: str
    reads: Set[str]
    writes: Set[str]
    runner: Runner
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None
    write_specs: Dict[str, WriteSpec] | None = None
    read_consistency: str = "snapshot"
    cost_estimate: Dict[str, Any] | None = None
    tags: Set[str] | None = None
# Python runner implementation moved to core.runners.python_runner
