"""Scenario testing harness for replaying atom histories and asserting outcomes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.errors import GoalBlocked


@dataclass
class AtomRecord:
    """Materialized atom row fetched from the memory store."""

    id: str
    schema: Optional[str]
    modality: str
    content: Any
    facets: Dict[str, Any]
    provenance: Dict[str, Any]
    timestamp: int

    @property
    def label(self) -> str:
        return self.schema or self.modality or "(unknown)"


@dataclass
class CoverageMetrics:
    """Computed coverage information for the replayed scenario."""

    actual: float
    target: float
    passed: bool
    assumed: bool
    missing_files: List[str]
    source_atom: AtomRecord


@dataclass
class GoalBlockedSimulation:
    """Synthetic GoalBlocked result derived from the replay."""

    reason: str
    details: Dict[str, Any]


@dataclass
class ScenarioReport:
    """Aggregate outcome for a scenario replay."""

    ok: bool
    coverage: Optional[CoverageMetrics]
    goal_blocked: Optional[GoalBlockedSimulation]
    atoms: Sequence[AtomRecord]

    def to_dict(self) -> Dict[str, Any]:
        coverage_payload: Optional[Dict[str, Any]]
        if self.coverage is None:
            coverage_payload = None
        else:
            coverage_payload = {
                "actual": self.coverage.actual,
                "target": self.coverage.target,
                "passed": self.coverage.passed,
                "assumed": self.coverage.assumed,
                "missing_files": self.coverage.missing_files,
                "source_atom": self.coverage.source_atom.id,
            }

        goal_blocked_payload: Optional[Dict[str, Any]]
        if self.goal_blocked is None:
            goal_blocked_payload = None
        else:
            goal_blocked_payload = {
                "reason": self.goal_blocked.reason,
                "details": self.goal_blocked.details,
            }

        return {
            "ok": self.ok,
            "coverage": coverage_payload,
            "goal_blocked": goal_blocked_payload,
            "atoms": [
                {
                    "id": atom.id,
                    "schema": atom.schema,
                    "modality": atom.modality,
                    "timestamp": atom.timestamp,
                    "facets": atom.facets,
                }
                for atom in self.atoms
            ],
        }


class ScenarioHarness:
    """Replay helper for analysing stored atoms and expected outcomes."""

    def __init__(self, atoms: Sequence[AtomRecord]) -> None:
        self._atoms: List[AtomRecord] = list(atoms)

    @classmethod
    def from_sqlite(
        cls,
        db_path: Path | str,
        *,
        domain: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> "ScenarioHarness":
        """Load atoms from a SQLite memory store."""

        connection = sqlite3.connect(str(db_path))
        connection.row_factory = sqlite3.Row
        try:
            conditions: List[str] = []
            params: List[Any] = []
            if domain:
                conditions.append("domain = ?")
                params.append(domain)
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = (
                "SELECT id, modality, content, schema, facets, provenance, created_at "
                "FROM atoms WHERE " + where_clause + " ORDER BY created_at ASC"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(int(limit))

            cursor = connection.cursor()
            try:
                rows = cursor.execute(sql, params).fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()

        atoms = [cls._row_to_atom(row) for row in rows]
        return cls(atoms)

    @staticmethod
    def _row_to_atom(row: sqlite3.Row) -> AtomRecord:
        content_raw = row["content"] or "null"
        facets_raw = row["facets"] or "{}"
        provenance_raw = row["provenance"] or "{}"

        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            content = content_raw

        try:
            facets = json.loads(facets_raw)
        except json.JSONDecodeError:
            facets = {}

        try:
            provenance = json.loads(provenance_raw)
        except json.JSONDecodeError:
            provenance = {}

        timestamp = int(row["created_at"] or 0)

        return AtomRecord(
            id=row["id"],
            schema=row["schema"],
            modality=row["modality"],
            content=content,
            facets=facets,
            provenance=provenance,
            timestamp=timestamp,
        )

    @property
    def atoms(self) -> Sequence[AtomRecord]:
        return tuple(self._atoms)

    def generate_report(self, *, coverage_target: Optional[float] = None) -> ScenarioReport:
        coverage = self._extract_coverage(coverage_target)
        blocked: Optional[GoalBlockedSimulation] = None
        ok = True

        if coverage and not coverage.passed:
            blocked = GoalBlockedSimulation(
                reason="coverage_shortfall",
                details={
                    "target": coverage.target,
                    "actual": coverage.actual,
                    "missing_files": coverage.missing_files,
                    "assumed": coverage.assumed,
                    "source_atom": coverage.source_atom.id,
                },
            )
            ok = False

        return ScenarioReport(ok=ok, coverage=coverage, goal_blocked=blocked, atoms=self.atoms)

    def assert_coverage(self, *, coverage_target: Optional[float] = None) -> None:
        """Raise GoalBlocked if coverage is below the supplied or inferred target."""

        report = self.generate_report(coverage_target=coverage_target)
        if report.coverage is None:
            raise GoalBlocked(
                "no_execution_data",
                details={"message": "No execution atoms were found in the memory store."},
            )
        if not report.coverage.passed:
            details = report.goal_blocked.details if report.goal_blocked else {}
            raise GoalBlocked("coverage_shortfall", details=details)

    def render_timeline(self, *, width: int = 50) -> List[str]:
        """Return an ASCII timeline representation of the loaded atoms."""

        if not self._atoms:
            return []

        timestamps = [a.timestamp for a in self._atoms]
        first_ts = min(timestamps)
        last_ts = max(timestamps)
        span = max(last_ts - first_ts, 1)

        lines: List[str] = []
        for atom in self._atoms:
            position = int(((atom.timestamp - first_ts) / span) * (width - 1)) if span else 0
            bar = " " * max(position, 0) + "●"
            ts_iso = datetime.fromtimestamp(atom.timestamp / 1000.0).isoformat(timespec="seconds")
            lines.append(f"{ts_iso} {bar:<{width}} {atom.label}")
        return lines

    def _extract_coverage(self, coverage_target: Optional[float]) -> Optional[CoverageMetrics]:
        execution_atom = self._latest_execution_atom()
        if not execution_atom:
            return None

        payload = execution_atom.content if isinstance(execution_atom.content, dict) else {}
        coverage_data = payload.get("coverage") if isinstance(payload, dict) else None
        status = payload.get("status") if isinstance(payload, dict) else None

        assumed = False
        missing_files: List[str] = []

        if isinstance(coverage_data, dict):
            totals = coverage_data.get("totals", {})
            try:
                actual = float(totals.get("percent_covered", 0.0)) / 100.0
            except (TypeError, ValueError):
                actual = 0.0
            files = coverage_data.get("files", {})
            if isinstance(files, dict):
                for path, info in files.items():
                    if isinstance(info, dict) and info.get("missing_lines"):
                        missing_files.append(str(path))
        else:
            assumed = True
            actual = 1.0 if status == "passed" else 0.0

        target = coverage_target
        if target is None:
            target = self._infer_target(actual)
        if target is None:
            target = 0.0

        passed = actual >= float(target)

        return CoverageMetrics(
            actual=actual,
            target=float(target),
            passed=passed,
            assumed=assumed,
            missing_files=missing_files,
            source_atom=execution_atom,
        )

    def _latest_execution_atom(self) -> Optional[AtomRecord]:
        for atom in reversed(self._atoms):
            if atom.schema == "testwriter.execution@v1":
                return atom
        return None

    def _infer_target(self, fallback_actual: float) -> Optional[float]:
        keys = {"coverage_target", "target", "coverageGoal"}
        for atom in reversed(self._atoms):
            content = atom.content
            if isinstance(content, dict):
                for key in keys:
                    value = content.get(key)
                    if isinstance(value, (int, float)):
                        return float(value)
                coverage = content.get("coverage")
                if isinstance(coverage, dict):
                    target = coverage.get("target")
                    if isinstance(target, (int, float)):
                        return float(target)
        # fallback: if coverage assumed from pass/fail, treat 1.0 as success baseline
        if fallback_actual in (0.0, 1.0):
            return 1.0 if fallback_actual == 1.0 else 0.0
        return None


__all__ = [
    "AtomRecord",
    "CoverageMetrics",
    "GoalBlockedSimulation",
    "ScenarioHarness",
    "ScenarioReport",
]
