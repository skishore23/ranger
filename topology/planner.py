"""Deterministic planner selecting registered regions for execution."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .registry import list_regions
from .types import Budget, Path, Region


DEFAULT_MAX_REGIONS: Dict[str, int] = {"memory": 2, "guard": 3, "model": 1, "tool": 1}
DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "latency": 0.75,
    "tokens": 0.02,
    "risk": 10.0,
    "trust": 8.0,
}
GUARD_MODE_PRIORITY = {"block": 0, "mask": 1, "allow": 2}
_REQUIRED_SCORE_KEYS = frozenset(DEFAULT_SCORE_WEIGHTS.keys())


@dataclass(frozen=True)
class PlannerConfig:
    """Configuration for planner scoring and region limits."""

    max_regions: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_MAX_REGIONS))
    score_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS))

    def __post_init__(self) -> None:
        for kind, value in self.max_regions.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"max_regions[{kind!r}] must be a non-negative integer")

        missing = _REQUIRED_SCORE_KEYS - set(self.score_weights.keys())
        if missing:
            raise ValueError(f"score_weights missing required keys: {sorted(missing)}")

        for key, value in self.score_weights.items():
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"score_weights[{key!r}] must be a finite number")


def _unit_tags(unit: Any) -> Set[str]:
    tags = getattr(unit, "tags", None)
    if not tags:
        return set()
    return set(tags)


def _cost_profile(region: Region) -> Dict[str, float]:
    profile = getattr(region, "cost_profile", {}) or {}
    trust = getattr(region, "trust", profile.get("trust", 0.5))
    return {
        "latency": float(profile.get("latency", 10.0)),
        "tokens": float(profile.get("tokens", 100.0)),
        "risk": float(profile.get("risk", 0.5)),
        "trust": float(trust),
    }


def _region_score(region: Region, config: PlannerConfig) -> float:
    profile = _cost_profile(region)
    w = config.score_weights
    return (
        profile["latency"] * float(w["latency"])
        + profile["tokens"] * float(w["tokens"])
        + profile["risk"] * float(w["risk"])
        - profile["trust"] * float(w["trust"])
    )


def _guard_sort_key(region: Region, config: PlannerConfig) -> tuple:
    mode = getattr(region, "mode", "mask")
    severity = getattr(region, "severity", GUARD_MODE_PRIORITY.get(mode, 1))
    return (
        GUARD_MODE_PRIORITY.get(mode, 1),
        severity,
        _region_score(region, config),
    )


def _budget_allows(kind: str, budget: Optional[Budget]) -> bool:
    if budget is None:
        return True
    if kind in {"model", "tool"} and budget.calls <= 0:
        return False
    if kind == "model" and budget.tokens <= 0:
        return False
    if kind == "tool" and budget.ms <= 0:
        return False
    return True


def _region_applicable(region: Region, *, goal_domain: Optional[str], required_tags: Set[str]) -> bool:
    region_domain = getattr(region, "domain", None)
    if goal_domain and region_domain and region_domain not in {goal_domain, "global"}:
        return False

    required_region_tags: Iterable[str] = getattr(region, "tags", ()) or ()
    if required_region_tags and not required_tags.issuperset(set(required_region_tags)):
        return False

    return True


def _select_regions(
    kind: str,
    sorter: Callable[[Region], Any],
    *,
    goal_domain: Optional[str],
    required_tags: Set[str],
    config: PlannerConfig,
) -> List[Region]:
    regions = [
        region
        for region in list(list_regions(kind))
        if _region_applicable(region, goal_domain=goal_domain, required_tags=required_tags)
    ]
    regions.sort(key=sorter)
    limit = config.max_regions.get(kind, len(regions))
    return regions[:limit]


def _goal_domain(goal: Any) -> Optional[str]:
    if isinstance(goal, dict):
        val = goal.get("domain")
        return str(val) if val is not None else None
    domain = getattr(goal, "domain", None)
    return str(domain) if domain is not None else None


def plan_path(
    unit: Any,
    goal: Dict[str, Any],
    budget: Budget | None = None,
    config: PlannerConfig | None = None,
) -> Path:
    """Return a cost-aware path grouping regions by kind."""

    planner_config = config or PlannerConfig()
    goal_domain = _goal_domain(goal)

    tags = _unit_tags(unit)
    requires_model = "llm" in tags
    requires_tools = "action" in tags and "llm" not in tags

    memory_regions = _select_regions(
        "memory",
        lambda region: _region_score(region, planner_config),
        goal_domain=goal_domain,
        required_tags=tags,
        config=planner_config,
    )
    guard_regions = _select_regions(
        "guard",
        lambda region: _guard_sort_key(region, planner_config),
        goal_domain=goal_domain,
        required_tags=tags,
        config=planner_config,
    )

    model_regions: List[Region] = []
    if requires_model and _budget_allows("model", budget):
        model_regions = _select_regions(
            "model",
            lambda region: _region_score(region, planner_config),
            goal_domain=goal_domain,
            required_tags=tags,
            config=planner_config,
        )

    tool_regions: List[Region] = []
    if requires_tools and _budget_allows("tool", budget):
        tool_regions = _select_regions(
            "tool",
            lambda region: _region_score(region, planner_config),
            goal_domain=goal_domain,
            required_tags=tags,
            config=planner_config,
        )

    coverage = {
        "read": bool(memory_regions),
        "validate": bool(guard_regions),
        "infer": bool(model_regions),
        "act": bool(tool_regions),
    }

    total_cost = sum(
        _region_score(region, planner_config)
        for region in (memory_regions + guard_regions + model_regions + tool_regions)
    )

    return Path(
        memory_like=memory_regions,
        guards=guard_regions,
        models=model_regions,
        tools=tool_regions,
        cost=total_cost,
        coverage=coverage,
    )


__all__ = ["plan_path", "PlannerConfig", "DEFAULT_MAX_REGIONS", "DEFAULT_SCORE_WEIGHTS"]
