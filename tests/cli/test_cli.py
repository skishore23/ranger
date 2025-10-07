"""Tests for the Ranger developer CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ranger.cli import app


runner = CliRunner()


def _touch_sqlite(db_path: Path) -> None:
    """Create a simple atoms table with a single row for trace tests."""

    connection = sqlite3.connect(str(db_path))
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE atoms (
                id TEXT PRIMARY KEY,
                modality TEXT NOT NULL,
                content TEXT NOT NULL,
                schema TEXT,
                facets TEXT NOT NULL,
                provenance TEXT NOT NULL,
                policy TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                domain TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO atoms (id, modality, content, schema, facets, provenance, policy, created_at, domain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "atom-1",
                "text",
                json.dumps({"message": "hello"}),
                "tests.record@v1",
                json.dumps({"unit": "demo.step", "goal": "demo"}),
                json.dumps({"parents": []}),
                json.dumps({"pii": False}),
                1_700_000_000_000,
                "demo",
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def _prepare_execution_db(
    db_path: Path,
    *,
    coverage_percent: float,
    target: float,
    status: str = "passed",
) -> None:
    """Populate the SQLite store with execution and config atoms."""

    connection = sqlite3.connect(str(db_path))
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS atoms (
                id TEXT PRIMARY KEY,
                modality TEXT NOT NULL,
                content TEXT NOT NULL,
                schema TEXT,
                facets TEXT NOT NULL,
                provenance TEXT NOT NULL,
                policy TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                domain TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO atoms (id, modality, content, schema, facets, provenance, policy, created_at, domain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "config-1",
                "json",
                json.dumps({"coverage_target": target}),
                "testwriter.config@v1",
                json.dumps({"domain": "demo"}),
                json.dumps({"parents": []}),
                json.dumps({}),
                1_700_000_000_100,
                "demo",
            ),
        )
        cursor.execute(
            """
            INSERT INTO atoms (id, modality, content, schema, facets, provenance, policy, created_at, domain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "exec-1",
                "json",
                json.dumps(
                    {
                        "status": status,
                        "coverage": {
                            "totals": {"percent_covered": coverage_percent},
                            "files": {},
                        },
                    }
                ),
                "testwriter.execution@v1",
                json.dumps({"domain": "demo"}),
                json.dumps({"parents": []}),
                json.dumps({}),
                1_700_000_000_200,
                "demo",
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def test_cli_init_scaffolds_agent(tmp_path: Path) -> None:
    """`ranger init` should scaffold a new agent package."""

    result = runner.invoke(app, ["init", "demo-agent", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    agent_root = tmp_path / "agents" / "demo_agent"
    assert (agent_root / "agent.py").exists()
    assert (agent_root / "capabilities.py").exists()
    assert (tmp_path / "tests" / "test_demo_agent.py").exists()


def test_cli_trace_reads_database(tmp_path: Path) -> None:
    """`ranger trace` should display rows from the atoms table."""

    db_path = tmp_path / "atoms.db"
    _touch_sqlite(db_path)

    result = runner.invoke(app, ["trace", str(db_path), "--domain", "demo", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert "tests.record@v1" in result.output
    assert "demo.step" in result.output


def test_cli_scenario_reports_success(tmp_path: Path) -> None:
    """`ranger scenario` should report success when coverage exceeds the target."""

    db_path = tmp_path / "atoms.db"
    _prepare_execution_db(db_path, coverage_percent=72.0, target=0.6, status="passed")

    result = runner.invoke(
        app,
        [
            "scenario",
            str(db_path),
            "--domain",
            "demo",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["coverage"]["passed"] is True


def test_cli_scenario_reports_failure(tmp_path: Path) -> None:
    """`ranger scenario` exits with code 1 when coverage misses the target."""

    db_path = tmp_path / "atoms.db"
    _prepare_execution_db(db_path, coverage_percent=30.0, target=0.8, status="failed")

    result = runner.invoke(
        app,
        [
            "scenario",
            str(db_path),
            "--domain",
            "demo",
        ],
    )
    assert result.exit_code == 1
    assert "GoalBlocked simulated" in result.stdout


def test_cli_visualize_stub(tmp_path: Path) -> None:
    """`ranger visualize` should create a placeholder graph when Graphviz is available."""

    pytest.importorskip("graphviz")
    from graphviz.backend import ExecutableNotFound

    output = tmp_path / "agent_graph"
    result = runner.invoke(
        app,
        [
            "visualize",
            "tests.fixtures.stub_capabilities:CAPABILITIES",
            "--output",
            str(output),
            "--format",
            "svg",
        ],
    )

    if isinstance(result.exception, ExecutableNotFound) or "Graphviz executable not found" in result.output:
        pytest.skip("Graphviz executable not available")

    assert result.exit_code == 0, result.output
    assert (output.with_suffix(".svg")).exists()
