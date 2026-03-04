"""Public entry point for the Deep Research agent."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from dotenv import load_dotenv

from agents.common.runtime import AgentRuntime
from core.plan import plan, action, Action
from core.llm.provider import register_llm_profile
from core.errors import GoalBlocked, SolveResult
from topology.registry import list_regions
from topology.types import Budget

from boot import setup_openai_llm
from ranger.scenario import ScenarioReport

from . import capabilities
from .types import DeepResearchConfig

load_dotenv()

MEMORY_KEY = "deepresearch.memory"
MEMORY_DOMAIN = "deepresearch"
LLM_KEY = "deepresearch.llm"
DB_FILENAME = "deepresearch.db"
DEFAULT_BUDGET = Budget(tokens=20000, ms=120000, calls=16)

logger = logging.getLogger(__name__)


class DeepResearchAgent(AgentRuntime):
    """High-level facade over the deep research capabilities."""

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        config: Optional[DeepResearchConfig | Dict[str, object]] = None,
        topic: Optional[str] = None,
        plugins: Optional[Iterable] = None,
        budget: Optional[Budget] = None,
        auto_visualize: Union[bool, str, Path, None] = None,
        **runtime_options: Any,
    ) -> None:
        self.topic = topic
        self.config = self._coerce_config(config)
        self.budget = budget or DEFAULT_BUDGET
        self._plugins = tuple(plugins or ())

        super().__init__(
            repo_root=repo_root,
            capability_list=None,
            plan=None,
            budget=self.budget,
            memory_key=MEMORY_KEY,
            memory_domain=MEMORY_DOMAIN,
            db_filename=DB_FILENAME,
            auto_visualize=auto_visualize,
            **runtime_options,
        )

    def build_plan(self):
        llm_cfg = self.config.llm
        ensure_system = llm_cfg.system_prompt or capabilities.DEFAULT_STAGE_SYSTEM["draft"]

        def _ensure_region() -> None:
            setup_openai_llm(
                key=LLM_KEY,
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                system_prompt=ensure_system,
            )

        defaults_base = {
            "model": llm_cfg.model,
            "temperature": llm_cfg.temperature,
            "max_tokens": llm_cfg.max_tokens,
        }

        register_llm_profile(
            "deepresearch.plan",
            region_key=LLM_KEY,
            defaults={**defaults_base, "system": capabilities.DEFAULT_STAGE_SYSTEM["plan"]},
            region_budget={"max_tokens": llm_cfg.max_tokens} if llm_cfg.max_tokens else None,
            region_factory=_ensure_region,
        )
        register_llm_profile(
            "deepresearch.notes",
            region_key=LLM_KEY,
            defaults={**defaults_base, "system": capabilities.DEFAULT_STAGE_SYSTEM["notes"]},
            region_budget={"max_tokens": llm_cfg.max_tokens} if llm_cfg.max_tokens else None,
            region_factory=_ensure_region,
        )
        register_llm_profile(
            "deepresearch.draft",
            region_key=LLM_KEY,
            defaults={**defaults_base, "system": ensure_system},
            region_budget={"max_tokens": llm_cfg.max_tokens} if llm_cfg.max_tokens else None,
            region_factory=_ensure_region,
        )

        config_initial = {"deepresearch.config"}
        capability_actions: List[Action] = [
            action(
                capabilities.solicit_topic,
                note="Gather or confirm research topic",
            ),
            action(
                capabilities.capture_request,
                requires_initial=config_initial,
            ),
            action(
                capabilities.design_research_plan,
                requires_initial=config_initial,
            ),
            action(
                capabilities.gather_sources,
                requires_initial=config_initial,
            ),
            action(
                capabilities.synthesize_notes,
                requires_initial=config_initial,
            ),
            action(
                capabilities.draft_report,
                requires_initial=config_initial,
            ),
            action(
                capabilities.ensure_feedback,
                requires_initial=config_initial,
            ),
            action(
                capabilities.integrate_feedback,
                requires_initial=config_initial,
            ),
            action(
                capabilities.finalize_report,
                requires_initial=config_initial,
            ),
            action(
                capabilities.persist_report,
                requires_initial=config_initial,
            ),
            action(
                capabilities.summarize_execution,
                requires_initial=config_initial,
            ),
        ]

        if self.config.require_human_feedback:
            capability_actions.insert(
                6,
                action(
                    capabilities.human_review,
                    note="Collect human feedback",
                ),
            )

        if self._plugins:
            capability_actions.extend(action(plugin) for plugin in self._plugins)

        return plan(*capability_actions)

    @staticmethod
    def _coerce_config(config: Optional[DeepResearchConfig | Dict[str, object]]) -> DeepResearchConfig:
        if config is None:
            return DeepResearchConfig()
        if isinstance(config, DeepResearchConfig):
            return config
        return DeepResearchConfig.from_state(config)

    @property
    def capabilities(self) -> Sequence:
        return super().capabilities

    def run(
        self,
        *,
        topic: Optional[str] = None,
        max_steps: int = 120,
        visualize: Union[bool, str, Path, None] = None,
    ) -> SolveResult:
        chosen_topic = topic or self.topic
        if not chosen_topic:
            raise ValueError("A research topic must be supplied via constructor or run(topic=...)")

        logger.info("Starting deep research agent")
        logger.debug("Repository: %s", self.repo_root)
        logger.debug("Topic: %s", chosen_topic)
        logger.debug("Regions: %s", [region.key for region in list_regions(None)])

        result = self.run_agent(
            initial={
                "deepresearch.config": self.config.to_state(),
                "research.topic": chosen_topic,
            },
            goal=capabilities.research_complete,
            max_steps=max_steps,
        )

        target = self._resolve_visualize_target(
            visualize,
            default_name="deep_research_agent_graph",
        )
        if target is not None:
            try:
                self.visualize(target, fmt="png")
            except RuntimeError as exc:
                logger.warning("Unable to render deep research graph: %s", exc)
        return result

    def visualize(self, output: Path, *, fmt: str = "png") -> Path:
        from core.visualization.graph import GraphvizUnavailable, render_capability_graph

        try:
            return render_capability_graph(
                self.capabilities,
                output,
                fmt=fmt,
                graph_name="deepresearch_agent",
                capability_color="#2B6CB0",
                state_color="#F7FAFC",
                read_edge_color="#2C5282",
                write_edge_color="#38A169",
            )
        except GraphvizUnavailable as exc:
            raise RuntimeError(str(exc)) from exc

    def scenario_report(self, *, coverage_target: Optional[float] = None) -> ScenarioReport:
        harness = self._load_harness()
        if harness is None or not harness.atoms:
            atoms = () if harness is None else harness.atoms
            return ScenarioReport(ok=False, coverage=None, goal_blocked=None, atoms=atoms)
        return harness.generate_report(coverage_target=coverage_target)

    def scenario_timeline(self) -> List[str]:
        return super().scenario_timeline()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ranger deep research agent")
    parser.add_argument("--topic", help="Topic to research", default=None)
    parser.add_argument("--repo", default=".", help="Workspace directory for artifacts")
    parser.add_argument("--max-steps", type=int, default=120, help="Maximum engine steps")
    args = parser.parse_args(list(argv) if argv is not None else None)

    agent = DeepResearchAgent(repo_root=args.repo, topic=args.topic)
    topic = args.topic or agent.topic or "Ten-year outlook for synthetic biology"
    try:
        result = agent.run(topic=topic, max_steps=args.max_steps)
    except GoalBlocked as blocked:
        print(f"Run blocked: {blocked.reason} -> {blocked.details}")
        return 1
    summary = result.final.value("research.summary") if result.final.exists("research.summary") else {}

    print("\n📋 Deep research summary")
    print(f" - ok: {result.ok}")
    print(f" - steps: {result.steps}")
    print(f" - blocker: {result.blocker}")
    if summary:
        print(f" - metrics: {summary}")

    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


DeepResearchAgent.__test__ = False  # type: ignore[attr-defined]
