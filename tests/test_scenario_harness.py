"""Tests for the scenario harness utilities."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from core.errors import GoalBlocked
from ranger.scenario import ScenarioHarness


def _init_db(db_path: Path) -> None:
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
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def _insert_atom(
    db_path: Path,
    *,
    atom_id: str,
    modality: str = "json",
    schema: str | None = None,
    content: dict | str | None = None,
    facets: dict | None = None,
    created_at: int = 1_700_000_000_000,
    domain: str = "demo",
) -> None:
    connection = sqlite3.connect(str(db_path))
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO atoms (id, modality, content, schema, facets, provenance, policy, created_at, domain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atom_id,
                modality,
                json.dumps(content if content is not None else {}),
                schema,
                json.dumps(facets if facets is not None else {}),
                json.dumps({"parents": []}),
                json.dumps({}),
                created_at,
                domain,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def test_harness_reports_passed_coverage(tmp_path: Path) -> None:
    db_path = tmp_path / "atoms.db"
    _init_db(db_path)
    _insert_atom(
        db_path,
        atom_id="config-1",
        schema="testwriter.config@v1",
        content={"coverage_target": 0.5},
        created_at=1_700_000_000_100,
    )
    coverage_payload = {
        "status": "passed",
        "returncode": 0,
        "coverage": {
            "totals": {"percent_covered": 75.0},
            "files": {},
        },
    }
    _insert_atom(
        db_path,
        atom_id="exec-1",
        schema="testwriter.execution@v1",
        content=coverage_payload,
        created_at=1_700_000_000_200,
    )

    harness = ScenarioHarness.from_sqlite(db_path)
    report = harness.generate_report()

    assert report.ok
    assert report.coverage is not None
    assert math.isclose(report.coverage.actual, 0.75, rel_tol=1e-5)
    assert math.isclose(report.coverage.target, 0.5, rel_tol=1e-5)
    assert report.coverage.passed
    assert not report.coverage.assumed


def test_harness_reports_goal_blocked_when_below_target(tmp_path: Path) -> None:
    db_path = tmp_path / "atoms.db"
    _init_db(db_path)
    _insert_atom(
        db_path,
        atom_id="config-1",
        schema="testwriter.config@v1",
        content={"coverage_target": 0.8},
        created_at=1_700_000_000_100,
    )
    coverage_payload = {
        "status": "failed",
        "returncode": 1,
        "coverage": {
            "totals": {"percent_covered": 45.0},
            "files": {"module.py": {"missing_lines": [1, 2]}},
        },
    }
    _insert_atom(
        db_path,
        atom_id="exec-1",
        schema="testwriter.execution@v1",
        content=coverage_payload,
        created_at=1_700_000_000_200,
    )

    harness = ScenarioHarness.from_sqlite(db_path)
    report = harness.generate_report()

    assert not report.ok
    assert report.goal_blocked is not None
    assert report.goal_blocked.reason == "coverage_shortfall"
    assert math.isclose(report.goal_blocked.details["target"], 0.8, rel_tol=1e-5)
    assert math.isclose(report.goal_blocked.details["actual"], 0.45, rel_tol=1e-5)
    assert "module.py" in report.goal_blocked.details["missing_files"]


def test_harness_assert_coverage_raises_goal_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "atoms.db"
    _init_db(db_path)
    _insert_atom(
        db_path,
        atom_id="exec-1",
        schema="testwriter.execution@v1",
        content={"status": "failed"},
        created_at=1_700_000_000_100,
    )

    harness = ScenarioHarness.from_sqlite(db_path)
    with pytest.raises(GoalBlocked) as excinfo:
        harness.assert_coverage(coverage_target=0.5)
    assert "coverage_shortfall" in str(excinfo.value)
