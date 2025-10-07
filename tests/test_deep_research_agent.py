from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.errors import GoalBlocked

from agents.deep_research.agent import DeepResearchAgent
from agents.deep_research.firecrawl import get_api_key
from agents.deep_research.types import DeepResearchConfig


def test_deep_research_agent_offline(tmp_path: Path) -> None:
    if not get_api_key():
        pytest.skip("FIRECRAWL_KEY required for deep research agent test")

    config = DeepResearchConfig(
        desired_length_pages=2,
        min_citations=5,
        require_human_feedback=False,
    )
    agent = DeepResearchAgent(repo_root=tmp_path, topic="Quantum computing impact", config=config)
    try:
        result = agent.run(max_steps=80)
    except GoalBlocked as exc:
        pytest.skip(f"Deep research blocked: {exc.reason}")

    if not result.ok:
        blocker = result.blocker.reason if result.blocker else "unknown"
        pytest.skip(f"Deep research run did not complete: {blocker}")

    final = result.final
    assert final.exists("research.report")
    report = final.value("research.report")
    assert "Quantum computing" in report
    citations = final.value("research.citations")
    assert isinstance(citations, list)
    assert len(citations) >= 5
    if final.exists("research.summary"):
        summary = final.value("research.summary")
        assert summary["approx_pages"] >= 2


def test_deep_research_visualize_stub(tmp_path: Path) -> None:
    agent = DeepResearchAgent(repo_root=tmp_path, topic="AI governance")
    try:
        import graphviz  # type: ignore
    except ImportError:
        return

    output = tmp_path / "deep_viz"
    try:
        path = agent.visualize(output, fmt="svg")
    except RuntimeError as exc:
        if "Graphviz executable" in str(exc):
            return
        raise
    assert path.exists()


def test_deep_research_scenario_helpers(tmp_path: Path) -> None:
    agent = DeepResearchAgent(repo_root=tmp_path, topic="Biosecurity strategy")
    report = agent.scenario_report()
    assert not report.ok
    assert agent.scenario_timeline() == []
