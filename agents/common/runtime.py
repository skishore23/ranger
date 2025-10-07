"""Shared runtime helpers for orchestrating Ranger agents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from boot import setup_memory
from core.errors import SolveResult
from core.sdk import Agent
from ranger.scenario import ScenarioHarness, ScenarioReport
from topology.registry import clear_registry
from topology.types import Budget

if TYPE_CHECKING:
    from core.plan import Plan


class AgentRuntime:
    """Reusable scaffold for agents that rely on persistent topology memory."""

    DB_DIR_NAME = Path(".ranger")

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        capability_list: Optional[Iterable] = None,
        plan: Optional["Plan"] = None,
        budget: Budget,
        memory_key: str,
        memory_domain: str,
        db_filename: str,
        reset_registry: bool = True,
        guard_regions: Optional[Iterable] = None,
        memory_kwargs: Optional[Dict[str, Any]] = None,
        auto_visualize: Union[bool, str, Path, None] = None,
    ) -> None:
        self._repo_root = Path(repo_root or Path.cwd()).resolve()
        self.memory_key = memory_key
        self.memory_domain = memory_domain
        self._db_path = self._ensure_db_path(db_filename)
        self._auto_visualize = auto_visualize

        if reset_registry:
            clear_registry()

        options: Dict[str, Any] = dict(memory_kwargs or {})
        options.setdefault("reset", reset_registry)
        options.setdefault("db_path", str(self._db_path))
        options.setdefault("memory_key", self.memory_key)
        options.setdefault("domain", self.memory_domain)
        options.setdefault("purge", True)
        if guard_regions is not None:
            options.setdefault("guards", list(guard_regions))
        setup_memory(**options)

        self.budget = budget

        builder_plan = plan or self.build_plan()
        self._plan = builder_plan

        if capability_list is not None:
            resolved_capabilities = list(capability_list)
            self._agent = Agent(resolved_capabilities, budget=self.budget)
        elif builder_plan is not None:
            self._agent = builder_plan.compile(budget=self.budget, strict=True)
            resolved_capabilities = list(self._agent.engine.capabilities)
        else:
            resolved_capabilities = list(self.build_capabilities())
            self._agent = Agent(resolved_capabilities, budget=self.budget)

        self._capability_list = resolved_capabilities

        self.configure_runtime()

    def _ensure_db_path(self, db_filename: str) -> Path:
        db_dir = self.repo_root / self.DB_DIR_NAME
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / db_filename

    def build_plan(self) -> Optional["Plan"]:
        return None

    def build_capabilities(self) -> Iterable:
        raise NotImplementedError(
            "Subclasses must implement build_plan() or build_capabilities()."
        )

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def capabilities(self) -> Sequence:
        return self._agent.engine.capabilities

    @property
    def capability_list(self) -> Sequence:
        return tuple(self._capability_list)

    @property
    def plan(self) -> Optional["Plan"]:
        return self._plan

    def build_initial_state(self, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        state = {"repo.root": str(self.repo_root)}
        if extra:
            state.update(extra)
        return state

    def run_agent(
        self,
        *,
        goal,
        max_steps: int,
        initial: Optional[Mapping[str, Any]] = None,
    ) -> SolveResult:
        if initial is not None and "repo.root" in initial:
            state = dict(initial)
        else:
            state = self.build_initial_state(initial)
        return self._agent.run(initial=state, goal=goal, max_steps=max_steps)

    def _resolve_visualize_target(
        self,
        override: Union[bool, str, Path, None],
        *,
        default_name: str,
    ) -> Optional[Path]:
        choice: Union[bool, str, Path, None] = self._auto_visualize if override is None else override
        if not choice:
            return None
        if isinstance(choice, (str, Path)):
            return Path(choice)
        return self.repo_root / default_name

    def _load_harness(self) -> Optional[ScenarioHarness]:
        if not self._db_path.exists():
            return None
        return ScenarioHarness.from_sqlite(self._db_path, domain=self.memory_domain)

    def scenario_report(self, *, coverage_target: Optional[float] = None) -> ScenarioReport:
        harness = self._load_harness()
        if harness is None:
            return ScenarioReport(ok=False, coverage=None, goal_blocked=None, atoms=())
        return harness.generate_report(coverage_target=coverage_target)

    def scenario_timeline(self) -> List[str]:
        harness = self._load_harness()
        if harness is None:
            return []
        return list(harness.render_timeline())

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def db_path(self) -> Path:
        return self._db_path

    def configure_runtime(self) -> None:
        """Hook for subclasses to register regions or additional setup."""

        return None
