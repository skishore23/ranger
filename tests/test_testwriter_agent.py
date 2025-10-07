from __future__ import annotations

from pathlib import Path

import pytest

from agents.testwriter.agent import TestWriterAgent


def test_capabilities_exposed(tmp_path: Path) -> None:
    agent = TestWriterAgent(repo_root=tmp_path)
    caps = agent.capabilities
    assert len(caps) > 0
    capability_names = {cap.id.split(".")[-1] for cap in caps}
    assert capability_names >= {
        "discover_source_files",
        "build_test_plan",
        "select_next_target",
    }


def test_visualize_requires_graphviz(tmp_path: Path) -> None:
    agent = TestWriterAgent(repo_root=tmp_path)
    try:
        pytest.importorskip("graphviz")
    except pytest.skip.Exception:
        pytest.skip("graphviz not installed")

    output = tmp_path / "viz"
    try:
        path = agent.visualize(output)
    except RuntimeError as exc:
        if "Graphviz executable" in str(exc):
            pytest.skip("graphviz executable missing")
        raise
    assert path.exists()


def test_scenario_report_missing_file(tmp_path: Path) -> None:
    agent = TestWriterAgent(repo_root=tmp_path)
    report = agent.scenario_report()
    assert not report.atoms
    assert report.coverage is None
    assert agent.scenario_timeline() == []
