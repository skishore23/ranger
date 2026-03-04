"""Public entry point for running the autonomous test writer agent."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from dotenv import load_dotenv

from agents.common.runtime import AgentRuntime
from core.plan import plan, action
from core.errors import SolveResult
from core.llm.provider import register_llm_profile

from boot import get_default_budget, setup_openai_llm
from topology.registry import list_regions
from topology.types import Budget

from ranger.scenario import ScenarioReport

from . import capabilities
from .types import TestWriterConfig


load_dotenv()

logger = logging.getLogger(__name__)


class TestWriterAgent(AgentRuntime):
    """High-level facade over the test writer capabilities."""

    MEMORY_KEY = "testwriter.memory"
    MEMORY_DOMAIN = "testwriter"
    DB_FILENAME = "testwriter.db"

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        config: Optional[TestWriterConfig | Dict[str, object]] = None,
        plugins: Optional[Iterable] = None,
        budget: Optional[Budget] = None,
        auto_visualize: Union[bool, str, Path, None] = None,
        **runtime_options: Any,
    ) -> None:
        self.config = self._coerce_config(config)
        self.budget = budget or get_default_budget()
        self._plugins = tuple(plugins or ())

        super().__init__(
            repo_root=repo_root,
            capability_list=None,
            plan=None,
            budget=self.budget,
            memory_key=self.MEMORY_KEY,
            memory_domain=self.MEMORY_DOMAIN,
            db_filename=self.DB_FILENAME,
            auto_visualize=auto_visualize,
            **runtime_options,
        )

    def build_plan(self):
        llm_config = self.config.llm or {}
        model = llm_config.get("model", "gpt-4o-mini")
        temperature = float(llm_config.get("temperature", 0.0))
        system_prompt = llm_config.get(
            "system_prompt", "You generate focused pytest test files as JSON."
        )
        max_tokens_raw = llm_config.get("max_tokens", 1600)
        max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None

        register_llm_profile(
            "testwriter.generation",
            region_key="testwriter.llm",
            defaults={
                "model": model,
                "temperature": temperature,
                "system": system_prompt,
                "max_tokens": max_tokens,
            },
            region_budget={"max_tokens": max_tokens} if max_tokens else None,
            region_factory=lambda: setup_openai_llm(
                key="testwriter.llm",
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
            ),
        )

        base_initial = {"repo.root", "testwriter.config"}
        capability_list: List = [
            action(
                capabilities.discover_source_files,
                requires_initial=base_initial,
                note="Index repository modules",
            ),
            action(
                capabilities.build_test_plan,
                requires_initial={"testwriter.config"},
                note="Prioritize uncovered modules",
            ),
            action(
                capabilities.select_next_target,
                requires_initial={"testwriter.config"},
            ),
            action(
                capabilities.gather_target_context,
                requires_initial={"repo.root", "testwriter.config"},
            ),
            action(
                capabilities.generate_test_candidates,
                requires_initial={"testwriter.config"},
            ),
            action(capabilities.prepare_write_requests),
            action(
                capabilities.write_test_files,
                requires_initial={"repo.root"},
            ),
            action(
                capabilities.run_selected_tests,
                requires_initial=base_initial,
            ),
            action(
                capabilities.analyze_coverage,
                requires_initial={"testwriter.config"},
            ),
            action(capabilities.summarize_test_execution),
            action(
                capabilities.repair_failed_tests,
                requires_initial={"testwriter.config"},
            ),
            action(
                capabilities.update_progress_trackers,
                requires_initial={"testwriter.config"},
            ),
            action(
                capabilities.detect_terminal_state,
                requires_initial={"testwriter.config"},
            ),
        ]

        if self._plugins:
            capability_list.extend(action(plugin) for plugin in self._plugins)

        return plan(*capability_list)

    @staticmethod
    def _coerce_config(config: Optional[TestWriterConfig | Dict[str, object]]) -> TestWriterConfig:
        if config is None:
            return TestWriterConfig()
        if isinstance(config, TestWriterConfig):
            return config
        return TestWriterConfig(**config)

    def run(
        self,
        *,
        max_steps: int = 200,
        visualize: Union[bool, str, Path, None] = None,
    ) -> SolveResult:
        """Execute the agent until tests are generated or the goal is met."""

        logger.info("Starting autonomous test writer")
        logger.debug("Repository: %s", self.repo_root)
        logger.debug("Config: %s", self.config)
        logger.debug("Regions: %s", [region.key for region in list_regions(None)])

        result = self.run_agent(
            initial={"testwriter.config": self.config.to_state()},
            goal=capabilities.tests_finished,
            max_steps=max_steps,
        )

        target = self._resolve_visualize_target(
            visualize,
            default_name="testwriter_agent_graph",
        )
        if target is not None:
            try:
                self.visualize(target, fmt="png")
            except RuntimeError as exc:
                logger.warning("Unable to render test writer graph: %s", exc)

        return result

    @property
    def capabilities(self) -> Sequence:
        """Expose the underlying capability list."""

        return super().capabilities

    def visualize(self, output: Path, *, fmt: str = "png") -> Path:
        """Render a capability/state graph for this agent using Graphviz."""

        from core.visualization.graph import GraphvizUnavailable, render_capability_graph

        try:
            return render_capability_graph(
                self.capabilities,
                output,
                fmt=fmt,
                graph_name="testwriter_agent",
                capability_color="#5A67D8",
                state_color="#EDF2F7",
                read_edge_color="#2C5282",
                write_edge_color="#2F855A",
            )
        except GraphvizUnavailable as exc:
            raise RuntimeError(str(exc)) from exc

    def scenario_report(self, *, coverage_target: Optional[float] = None) -> ScenarioReport:
        """Replay the latest run from the on-disk memory and compute coverage status."""

        return super().scenario_report(coverage_target=coverage_target)

    def scenario_timeline(self) -> List[str]:
        """Return an ASCII timeline of atoms written during the latest run."""

        return super().scenario_timeline()

    @staticmethod
    def summarize(result: SolveResult) -> Dict[str, object]:
        """Produce a user-friendly summary from the final state."""

        summary = {
            "ok": result.ok,
            "steps": result.steps,
            "blocker": result.blocker,
        }

        if result.final.exists("tests.summary"):
            summary["tests.summary"] = result.final.value("tests.summary")
        if result.final.exists("tests.completed"):
            summary["tests.completed"] = result.final.value("tests.completed")
        if result.final.exists("tests.failed"):
            summary["tests.failed"] = result.final.value("tests.failed")
        return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ranger test writer agent")
    parser.add_argument("--repo", default=".", help="Path to the repository under test")
    parser.add_argument("--max-steps", type=int, default=200, help="Maximum engine steps")
    args = parser.parse_args(list(argv) if argv is not None else None)

    agent = TestWriterAgent(repo_root=args.repo)
    result = agent.run(max_steps=args.max_steps)
    summary = TestWriterAgent.summarize(result)

    print("\n📋 Test writer summary")
    for key, value in summary.items():
        print(f" - {key}: {value}")

    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())


TestWriterAgent.__test__ = False  # type: ignore[attr-defined] - prevent pytest collection
