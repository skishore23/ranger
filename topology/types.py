"""Core data structures for the topology system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Protocol, Tuple

Modality = Literal["text", "code", "image", "table", "json", "log", "binary"]
RegionKind = Literal["memory", "guard", "model", "tool"]


@dataclass(frozen=True)
class Atom:
    """Content-addressed unit stored in topology regions."""

    id: str
    modality: Modality
    content: Any
    schema: Optional[str]
    facets: Dict[str, Any]
    provenance: Dict[str, Any]
    policy: Dict[str, Any]


@dataclass
class Budget:
    """Execution budget shared across planner and packer."""

    tokens: int
    ms: int
    calls: int
    by_modality: Optional[Dict[Modality, int]] = None


class Region(Protocol):
    """Unified interface implemented by all regions."""

    key: str
    kind: RegionKind

    def read(self, query: Dict[str, Any]) -> Iterable[Atom]: ...
    def write(self, atoms: Iterable[Atom]) -> None: ...
    def validate(self, atoms: Iterable[Atom]) -> Dict[str, Any]: ...
    def infer(
        self,
        prompt: Dict[str, Any],
        window: Iterable[Atom],
        budget: Dict[str, Any] | "Budget" | None = None,
    ) -> Iterable[Atom]: ...
    def act(self, window: Iterable[Atom]) -> Tuple[Iterable[Atom], Iterable[Dict[str, Any]]]: ...
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom: ...
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]: ...


@dataclass
class Path:
    """Planner output describing which regions to use."""

    memory_like: List[Region]
    guards: List[Region]
    models: List[Region]
    tools: List[Region]
    cost: float
    coverage: Dict[str, bool]


@dataclass
class ContextWindow:
    """Atoms selected for a unit execution."""

    atoms: List[Atom]
    budget_used: Budget
    regions_used: List[str]
    utility_score: float


__all__ = [
    "Atom",
    "Budget",
    "Region",
    "Path",
    "ContextWindow",
    "Modality",
    "RegionKind",
]
