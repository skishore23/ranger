"""Developer CLI for the Ranger framework."""

from __future__ import annotations

import importlib
import inspect
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import typer

from core.capability import Capability
from core.sdk import Agent
from core.visualization.graph import GraphvizUnavailable, render_capability_graph
from ranger.scenario import ScenarioHarness
from ranger.scaffold import (
    create_agent_scaffold,
    _slugify,
    _camelize as _camelize,
    _write_file as _write_file,
)

app = typer.Typer(help="Utility commands for scaffolding agents and inspecting Ranger runs.")


@app.command()
def init(
    name: str = typer.Argument(..., help="Name of the agent package (e.g. demo-agent)"),
    path: Path = typer.Option(Path("."), "--path", "-p", file_okay=False, dir_okay=True, help="Destination directory"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files if present"),
) -> None:
    """Scaffold a new Ranger agent (capabilities, types, agent entrypoint, tests)."""

    package = _slugify(name)
    agent_dir = path / "agents" / package
    if agent_dir.exists() and not force:
        raise typer.BadParameter(f"Agent package already exists: {agent_dir}")

    typer.secho(f"Creating agent scaffold for '{name}' in {agent_dir}", fg=typer.colors.GREEN)

    create_agent_scaffold(name, path, force=force)

    typer.secho("Done!", fg=typer.colors.GREEN)
    typer.echo(
        "Next steps:\n"
        f"  1. cd {path}\n"
        f"  2. python -m pytest tests/test_{package}.py\n"
        f"  3. python -m agents.{package}.agent"
    )


def _json_summary(text: str, *, max_length: int = 80) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text

    if isinstance(payload, (dict, list)):
        summary = json.dumps(payload, ensure_ascii=False)
    else:
        summary = str(payload)

    summary = summary.replace("\n", " ")
    if len(summary) > max_length:
        summary = summary[: max_length - 3] + "..."
    return summary


def _parse_timestamp(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid ISO timestamp: {value}") from exc


@app.command()
def trace(
    db_path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True, help="Path to the SQLite memory database"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Filter by domain facet"),
    schema: Optional[str] = typer.Option(None, "--schema", help="Filter by schema"),
    modality: Optional[str] = typer.Option(None, "--modality", help="Filter by modality"),
    unit: Optional[str] = typer.Option(None, "--unit", help="Filter by unit id"),
    goal: Optional[str] = typer.Option(None, "--goal", help="Filter by goal name"),
    since: Optional[str] = typer.Option(None, "--since", help="Earliest ISO timestamp (inclusive)"),
    until: Optional[str] = typer.Option(None, "--until", help="Latest ISO timestamp (inclusive)"),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum number of atoms to display"),
    timeline: bool = typer.Option(False, "--timeline", help="Render a simple ASCII timeline"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSON blobs"),
) -> None:
    """Inspect atoms stored in a SQLite memory region."""

    since_ms = _parse_timestamp(since)
    until_ms = _parse_timestamp(until)

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    conditions: List[str] = []
    params: List[object] = []

    if domain:
        conditions.append("domain = ?")
        params.append(domain)
    if schema:
        conditions.append("schema = ?")
        params.append(schema)
    if modality:
        conditions.append("modality = ?")
        params.append(modality)
    if since_ms is not None:
        conditions.append("created_at >= ?")
        params.append(since_ms)
    if until_ms is not None:
        conditions.append("created_at <= ?")
        params.append(until_ms)
    if unit:
        conditions.append("json_extract(facets, '$.unit') = ?")
        params.append(unit)
    if goal:
        conditions.append("json_extract(facets, '$.goal') = ?")
        params.append(goal)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT id, modality, schema, content, facets, provenance, created_at
        FROM atoms
        WHERE {where_clause}
        ORDER BY created_at ASC
        LIMIT ?
    """
    params.append(limit)

    cursor = connection.cursor()

    try:
        rows = cursor.execute(sql, params).fetchall()
    finally:
        cursor.close()
        connection.close()

    if not rows:
        typer.secho("No atoms found for the specified filters.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    if raw:
        for row in rows:
            payload = {
                "id": row["id"],
                "schema": row["schema"],
                "modality": row["modality"],
                "facets": json.loads(row["facets"]),
                "provenance": json.loads(row["provenance"]),
                "content": row["content"],
                "created_at": row["created_at"],
            }
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0)

    header = f"{'#':>3}  {'timestamp':<24} {'kind':<24} summary"
    typer.secho(header, fg=typer.colors.CYAN)
    typer.secho("-" * len(header), fg=typer.colors.CYAN)

    timestamps = [row["created_at"] for row in rows]
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    span = max(last_ts - first_ts, 1)

    for idx, row in enumerate(rows, start=1):
        kind = row["schema"] or row["modality"] or "(unknown)"
        facets = json.loads(row["facets"])
        content_summary = _json_summary(row["content"])
        ts_iso = datetime.fromtimestamp(row["created_at"] / 1000.0).isoformat(timespec="seconds")
        path_hint = facets.get("path") or facets.get("unit")
        summary = f"{content_summary}"
        if path_hint:
            summary = f"{path_hint} | {summary}"
        typer.echo(f"{idx:>3}  {ts_iso:<24} {kind:<24} {summary}")

    if timeline:
        typer.echo()
        typer.secho("Timeline", fg=typer.colors.MAGENTA)
        width = 50
        for row in rows:
            position = int(((row["created_at"] - first_ts) / span) * (width - 1))
            bar = " " * position + "●"
            label = row["schema"] or row["modality"] or "(unknown)"
            ts_iso = datetime.fromtimestamp(row["created_at"] / 1000.0).isoformat(timespec="seconds")
            typer.echo(f"{ts_iso} {bar:<{width}} {label}")


@app.command()
def scenario(
    db_path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True, help="Path to the SQLite memory database"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Select execution run (default: latest)"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Filter atoms by domain"),
    coverage_target: Optional[float] = typer.Option(None, "--coverage-target", help="Override the inferred coverage target (0-1 range)"),
    timeline: bool = typer.Option(False, "--timeline", help="Render an ASCII timeline of atom emission"),
    json_output: bool = typer.Option(False, "--json", help="Emit the scenario report as JSON"),
) -> None:
    """Replay a scenario and assert coverage expectations."""

    try:
        harness = ScenarioHarness.from_sqlite(db_path, domain=domain, run_id=run_id)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not harness.atoms and harness.execution is None:
        typer.secho("No atoms found for the specified filters.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    report = harness.generate_report(coverage_target=coverage_target)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.secho("Scenario Summary", fg=typer.colors.CYAN)
        if report.run_id:
            typer.echo(f"  Run {report.run_id}: {report.status}")
        if report.coverage:
            coverage_pct = report.coverage.actual * 100.0
            target_pct = report.coverage.target * 100.0
            assumed_note = " (assumed)" if report.coverage.assumed else ""
            status = "[OK]" if report.coverage.passed else "[FAIL]"
            typer.echo(
                f"  {status} coverage {coverage_pct:.1f}% vs target {target_pct:.1f}%{assumed_note}"
            )
            if report.coverage.missing_files:
                typer.echo(
                    "  Missing files: " + ", ".join(sorted(report.coverage.missing_files))
                )
        elif report.run_id is None:
            typer.secho("  No execution atoms detected.", fg=typer.colors.YELLOW)

        if report.goal_blocked:
            typer.secho("  GoalBlocked simulated:", fg=typer.colors.RED)
            details = report.goal_blocked.details
            typer.echo(
                f"    reason={report.goal_blocked.reason}"
                f" target={details.get('target')}"
                f" actual={details.get('actual')}"
            )

    if timeline:
        lines = harness.render_timeline()
        if lines:
            typer.echo()
            typer.secho("Timeline", fg=typer.colors.MAGENTA)
            for line in lines:
                typer.echo(line)

    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def replay(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Reconstruct a recorded run without executing capabilities."""
    from core.trace import replay_run
    try:
        result = replay_run(db_path, run_id)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Run {result['run_id']}: {result['status']}")
        typer.echo(json.dumps(result['state'], ensure_ascii=False, indent=2))


@app.command()
def explain(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Select run (default: latest)"),
    json_output: bool = typer.Option(False, "--json", help="Emit recorded scheduling decisions as JSON"),
) -> None:
    """Explain recorded scheduling decisions without rerunning tools or goals."""
    from core.explain import explain_run
    try:
        report = explain_run(db_path, run_id)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Run {report['run_id']}: {report['status']}")
    for schedule in report['schedules']:
        typer.echo(f"Step {schedule['step']}:")
        if schedule['stop']:
            typer.echo(f"  Execution stopped: {schedule['stop']['reason']} {schedule['stop']['details']}")
        for row in schedule['capabilities']:
            details = []
            if row['missing']:
                details.append('missing=' + ', '.join(row['missing']))
            for conflict in row['conflicts']:
                details.append(f"conflicts with {conflict['capability']} on {', '.join(conflict['keys'])}")
            if row['attempts']:
                details.append(f"failed attempts={row['attempts']}")
            suffix = f" ({'; '.join(details)})" if details else ''
            typer.echo(f"  {row['capability']}: {row['status']} — {row['reason']}{suffix}")
    if not report['schedules']:
        typer.echo('No scheduling decisions recorded; the initial goal may have been met or this is an older log.')
    checked = report['goal']
    if checked:
        typer.echo(f"Final goal: {checked['status']}")
        if checked.get('missing'):
            typer.echo('  Missing goal keys: ' + ', '.join(checked['missing']))
        for requirement in checked.get('unmet', []):
            typer.echo(f"  Unmet: {requirement}")
        if checked.get('reason') or checked.get('error'):
            typer.echo(f"  {checked.get('reason') or checked.get('error')}")
    if report['blocker']:
        typer.echo(f"Run blocked: {report['blocker']['reason']}")
    if report['error']:
        typer.echo(f"Run failed: {report['error']['type']}: {report['error']['message']}")


@app.command()
def trajectory(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    run_id: Optional[str] = typer.Option(None, '--run-id'),
    json_output: bool = typer.Option(False, '--json'),
) -> None:
    """Inspect versioned transitions without executing the workflow."""
    from core.transitions import trajectory_run
    try:
        report = trajectory_run(db_path, run_id)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Run {report['run_id']}: {report['status']}")
    for record in report['transitions']:
        typer.echo(f"{record['id']} {record['writer']}: {record['status']}")
        for key, change in record['changes'].items():
            old = change['before']['version'] if change['before'] else 'absent'
            typer.echo(f"  {key}: {old} -> {change['after']['version']} (changed={change['changed']})")
        for check in record['checks']:
            typer.echo(f"  {check['name']}: {'passed' if check['met'] else 'rejected'}")
            for reason in check['unmet']:
                typer.echo(f'    {reason}')
    if report['record_version'] == 1:
        typer.echo('Legacy recording: native transition metadata was not recorded.')


@app.command()
def history(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    field: str = typer.Argument(...),
    run_id: Optional[str] = typer.Option(None, '--run-id'),
    json_output: bool = typer.Option(False, '--json'),
) -> None:
    """Inspect accepted field versions and rejected attempts."""
    from core.transitions import field_history
    try:
        report = field_history(db_path, field, run_id)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(f"Run {report['run_id']}, field {field}")
    for entry in report['entries']:
        typer.echo(f"v{entry['version']} {entry['writer'] or 'initial'}: {json.dumps(entry['value'], ensure_ascii=False)}")
    for record in report['rejections']:
        typer.echo(f"{record['id']} {record['writer']}: rejected")


@app.command()
def diff(
    db_path: Path = typer.Argument(..., exists=True, readable=True),
    left: str = typer.Option(..., "--left"),
    right: str = typer.Option(..., "--right"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Compare final recorded states of two runs."""
    from core.trace import diff_runs
    try:
        result = diff_runs(db_path, left, right)
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def visualize(
    agent: str = typer.Argument(
        ..., help="Python path to an agent or capability list (module:object)"
    ),
    repo: Path = typer.Option(Path("."), "--repo", help="Repository root for agent initialization"),
    output: Path = typer.Option(
        Path("ranger-agent"), "--output", "-o", help="Output file path without extension"
    ),
    fmt: str = typer.Option("png", "--format", "-f", help="Graphviz output format (png, svg, pdf, ...)"),
) -> None:
    """Render a placeholder visualization for an agent using Graphviz."""

    capabilities = _load_capabilities(agent, repo)
    try:
        rendered = render_capability_graph(
            capabilities,
            output,
            fmt=fmt,
            capability_color="#5A67D8",
            state_color="#EDF2F7",
            read_edge_color="#2C5282",
            write_edge_color="#2F855A",
        )
    except GraphvizUnavailable as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.secho(f"Visualization written to {rendered}", fg=typer.colors.GREEN)


def _load_capabilities(spec: str, repo: Path) -> Sequence[Capability]:
    module_path, _, attr = spec.partition(":")
    if not module_path:
        raise typer.BadParameter("Agent specification must include a module path (module[:object])")

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise typer.BadParameter(f"Unable to import module '{module_path}': {exc}") from exc

    target = getattr(module, attr, None) if attr else module
    if target is None:
        raise typer.BadParameter(f"Attribute '{attr}' not found in module '{module_path}'")

    capabilities = _coerce_to_capabilities(target, repo)
    if not capabilities:
        raise typer.BadParameter(
            "Could not extract capabilities from the provided specification. Provide a list of Capability objects, an Agent instance, or an agent class."  # noqa: E501
        )
    return capabilities


def _coerce_to_capabilities(obj: object, repo: Path) -> Sequence[Capability]:
    if isinstance(obj, Agent):
        return obj.engine.capabilities

    if isinstance(obj, list) and all(isinstance(item, Capability) for item in obj):
        return obj

    if inspect.isclass(obj):
        try:
            instance = obj(repo_root=repo)
        except TypeError:
            instance = obj()
        return _coerce_to_capabilities(instance, repo)

    if callable(obj):
        try:
            result = obj(repo_root=repo)
        except TypeError:
            result = obj()
        return _coerce_to_capabilities(result, repo)

    engine = getattr(obj, "engine", None)
    if engine and hasattr(engine, "capabilities"):
        caps = getattr(engine, "capabilities")
        if isinstance(caps, list) and all(isinstance(item, Capability) for item in caps):
            return caps

    if hasattr(obj, "_agent"):
        return _coerce_to_capabilities(getattr(obj, "_agent"), repo)

    return []


if __name__ == "__main__":  # pragma: no cover
    app()
