"""Deterministic planner selecting registered regions for execution."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .registry import list_regions
from .types import Budget, Path, Region


MAX_REGIONS: Dict[str, int] = {"memory": 2, "guard": 3, "model": 1, "tool": 1}

GUARD_MODE_PRIORITY = {"block": 0, "mask": 1, "allow": 2}


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


def _region_score(region: Region) -> float:
    profile = _cost_profile(region)
    # λ1·latency + λ2·token + λ3·risk − λ4·trust
    return (
        profile["latency"] * 0.75
        + profile["tokens"] * 0.02
        + profile["risk"] * 10.0
        - profile["trust"] * 8.0
    )


def _guard_sort_key(region: Region) -> tuple:
    mode = getattr(region, "mode", "mask")
    severity = getattr(region, "severity", GUARD_MODE_PRIORITY.get(mode, 1))
    return (
        GUARD_MODE_PRIORITY.get(mode, 1),
        severity,
        _region_score(region),
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
) -> List[Region]:
    regions = [
        region
        for region in list(list_regions(kind))
        if _region_applicable(region, goal_domain=goal_domain, required_tags=required_tags)
    ]
    regions.sort(key=sorter)
    limit = MAX_REGIONS.get(kind, len(regions))
    return regions[:limit]


def plan_path(unit: Any, goal: Dict[str, Any], budget: Budget | None = None) -> Path:
    """Return a cost-aware path grouping regions by kind."""

    goal_domain: Optional[str] = None
    if isinstance(goal, dict):
        goal_domain = goal.get("domain")  # type: ignore[assignment]

    tags = _unit_tags(unit)
    requires_model = "llm" in tags
    requires_tools = "action" in tags and "llm" not in tags

    memory_regions = _select_regions(
        "memory",
        _region_score,
        goal_domain=goal_domain,
        required_tags=tags,
    )
    guard_regions = _select_regions(
        "guard",
        _guard_sort_key,
        goal_domain=goal_domain,
        required_tags=tags,
    )

    model_regions: List[Region] = []
    if requires_model and _budget_allows("model", budget):
        model_regions = _select_regions(
            "model",
            _region_score,
            goal_domain=goal_domain,
            required_tags=tags,
        )

    tool_regions: List[Region] = []
    if requires_tools and _budget_allows("tool", budget):
        tool_regions = _select_regions(
            "tool",
            _region_score,
            goal_domain=goal_domain,
            required_tags=tags,
        )

    coverage = {
        "read": bool(memory_regions),
        "validate": bool(guard_regions),
        "infer": bool(model_regions),
        "act": bool(tool_regions),
    }

    total_cost = sum(
        _region_score(region)
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


__all__ = ["plan_path"]
