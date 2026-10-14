"""Exercise shipped examples and real installs, never source-path fallbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[1]


def readme_example(title):
    section = (ROOT / "README.md").read_text().split(f"## {title}", 1)[1].split("\n## ", 1)[0]
    return re.search(r"```python\n(.*?)```", section, re.S).group(1)


@pytest.mark.parametrize("title", ["Hello, Ranger", "A Small ReAct-Style Agent"])
def test_literal_readme_workflows(title, tmp_path):
    # Catches broken Snapshot access, a missing provider and premature answer production.
    script = tmp_path / "example.py"
    script.write_text(readme_example(title))
    namespace = runpy.run_path(str(script), run_name="__main__")
    result = namespace["result"]
    assert result.ok, result
    if title.startswith("Hello"):
        assert result.final["c"] == 4
        assert (tmp_path / ".ranger/demo.db").exists()
    else:
        assert len(result.final["obs"]) == 2
        assert result.final["thought"]["thought"] == "answer"
        assert result.final["answer.draft"]["citations"] == ["fixture-1", "fixture-2"]
        assert "reasoning and acting" in result.final["answer.draft"]["text"]


def command(args, cwd, *, clean_stderr=False):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    if clean_stderr:
        assert not result.stderr, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def package_source(tmp_path_factory):
    work = tmp_path_factory.mktemp("package")
    source = work / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", ".venv", ".ranger", ".superpowers", "__pycache__", ".pytest_cache",
        ".env", "logs", "build", "dist", "*.egg-info", ".mypy_cache"))
    return work, source


@pytest.fixture(scope="module", params=["wheel", "editable"])
def installed(request, package_source):
    # Catches missing discovery/dependencies through actual build and installation.
    work, source = package_source
    mode = request.param
    target = work / mode
    venv.EnvBuilder().create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "Install uv to run the clean-install regressions"
    if mode == "wheel":
        dist = work / "wheels"
        command([sys.executable, "-m", "build", "--wheel", "--outdir", dist, source], work)
        install_args = [next(dist.glob("*.whl"))]
    else:
        install_args = ["--editable", source]
    command([uv, "pip", "install", "--python", python, *install_args, "pytest"], work)
    outside = work / f"outside-{mode}"
    outside.mkdir()
    cli = python.parent / ("ranger.exe" if os.name == "nt" else "ranger")
    return python, cli, outside, source, mode


def test_installed_imports_and_cli(installed):
    python, cli, outside, source, mode = installed
    output = command([python, "-c", "import json, core.sdk, ranger.cli, agents.deep_research.agent, "
                      "agents.testwriter.agent, regions, topology.types, boot, jinja2, jsonschema; "
                      "print(json.dumps([m.__file__ for m in [core.sdk, ranger.cli, "
                      "agents.deep_research.agent, agents.testwriter.agent, regions, topology.types, boot]]))"], outside)
    paths = json.loads(output)
    expected_root = source if mode == "editable" else python.parent.parent
    assert all(Path(path).is_relative_to(expected_root) for path in paths), paths
    assert all(not Path(path).is_relative_to(ROOT) for path in paths), paths
    assert "replay" in command([cli, "--help"], outside)


def test_installed_readme_replay_diff_and_scaffold(installed):
    python, cli, outside, source, mode = installed
    for title in ["Hello, Ranger", "A Small ReAct-Style Agent"]:
        script = outside / f"{title.split(',')[0].replace(' ', '_')}.py"
        script.write_text(readme_example(title))
        command([python, script], outside)
    first = json.loads(command([cli, "replay", ".ranger/demo.db", "--json"], outside))
    assert first["state"]["c"] == 4 and first["status"] == "succeeded"
    command([python, outside / "Hello.py"], outside)
    second = json.loads(command([cli, "replay", ".ranger/demo.db", "--json"], outside))
    assert first["run_id"] != second["run_id"]
    diff = json.loads(command([cli, "diff", ".ranger/demo.db", "--left", first["run_id"],
                               "--right", second["run_id"], "--json"], outside))
    assert diff["added"] == diff["removed"] == diff["changed"] == {}
    report = json.loads(command([cli, "scenario", ".ranger/demo.db", "--run-id",
                                 first["run_id"], "--json"], outside))
    assert report["ok"] is True
    command([cli, "init", "demo-agent"], outside)
    command([python, "-m", "pytest", "tests/test_demo_agent.py", "-q"], outside)
    command([python, "-c", "from agents.demo_agent import DemoAgentAgent; "
             "assert DemoAgentAgent().run().ok"], outside)
    output = command([python, "-m", "agents.demo_agent.agent"], outside, clean_stderr=True)
    assert "Ready to build with Ranger" in output
    run_id = output.split("Run ID: ", 1)[1].strip()
    scaffold = json.loads(command([cli, "replay", ".ranger/runs.db", "--run-id", run_id, "--json"], outside))
    assert scaffold["status"] == "succeeded"
    assert scaffold["state"]["demo_agent.message"].startswith("Ready to build with Ranger")
