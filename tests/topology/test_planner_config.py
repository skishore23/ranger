from dataclasses import dataclass, field

from topology import clear_registry, register_region
from topology.planner import PlannerConfig, plan_path
from topology.types import Budget


@dataclass
class StubRegion:
    key: str
    kind: str
    cost_profile: dict = field(default_factory=dict)
    trust: float = 0.5
    tags: set[str] = field(default_factory=set)
    domain: str = "global"
    mode: str = "mask"
    severity: int = 1


class Unit:
    tags = {"action"}


def test_plan_path_honors_max_regions_override():
    clear_registry()
    register_region(StubRegion("tool.fast", "tool", {"latency": 1, "tokens": 5, "risk": 0.2}))
    register_region(StubRegion("tool.backup", "tool", {"latency": 2, "tokens": 5, "risk": 0.2}))

    cfg = PlannerConfig(max_regions={"memory": 2, "guard": 3, "model": 1, "tool": 2})
    path = plan_path(Unit(), {"domain": "global"}, Budget(tokens=100, ms=100, calls=2), config=cfg)

    assert len(path.tools) == 2


def test_plan_path_honors_score_weights_override():
    clear_registry()
    register_region(StubRegion("tool.latency", "tool", {"latency": 1, "tokens": 1000, "risk": 0.1}))
    register_region(StubRegion("tool.tokens", "tool", {"latency": 10, "tokens": 1, "risk": 0.1}))

    default_path = plan_path(Unit(), {"domain": "global"}, Budget(tokens=1000, ms=1000, calls=1))
    assert default_path.tools[0].key == "tool.tokens"

    latency_heavy_cfg = PlannerConfig(score_weights={"latency": 5.0, "tokens": 0.001, "risk": 10.0, "trust": 8.0})
    custom_path = plan_path(
        Unit(),
        {"domain": "global"},
        Budget(tokens=1000, ms=1000, calls=1),
        config=latency_heavy_cfg,
    )
    assert custom_path.tools[0].key == "tool.latency"


def test_planner_config_validates_inputs():
    try:
        PlannerConfig(max_regions={"tool": -1}, score_weights={"latency": 1.0, "tokens": 0.1, "risk": 1.0, "trust": 1.0})
        assert False, "expected ValueError for negative max_regions"
    except ValueError:
        pass

    try:
        PlannerConfig(score_weights={"latency": 1.0, "tokens": 0.1, "risk": 1.0})
        assert False, "expected ValueError for missing score weight keys"
    except ValueError:
        pass
