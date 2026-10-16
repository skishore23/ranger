"""Generate the offline agent package independently of the CLI transport."""
from __future__ import annotations

from pathlib import Path
import re
import textwrap


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z-_]+", "-", name.strip())
    return slug.replace("-", "_").lower() or "agent"


def _camelize(name: str) -> str:
    parts = re.split(r"[-_\s]+", name)
    return "".join(part.capitalize() for part in parts if part)


def _write_file(path: Path, content: str, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"File already exists: {path}")
    path.write_text(content, encoding="utf-8")


def create_agent_scaffold(name: str, path: Path, *, force: bool = False) -> Path:
    """Write a starter package and smoke test, preserving existing files by default."""
    package = _slugify(name)
    class_name = f"{_camelize(name)}Agent"
    agent_dir = path / "agents" / package
    tests_dir = path / "tests"
    if agent_dir.exists() and not force:
        raise FileExistsError(f"Agent package already exists: {agent_dir}")

    __init_template = textwrap.dedent(
        """
        \"\"\"{name} agent package.\"\"\"

        __all__ = ["{class_name}"]


        def __getattr__(name):
            # Keep the public class import without preloading the module entrypoint.
            if name == "{class_name}":
                from .agent import {class_name}
                return {class_name}
            raise AttributeError(name)
        """
    ).strip()
    __init__ = __init_template.format(name=name, class_name=class_name) + "\n"

    capabilities_template = textwrap.dedent(
        """
        \"\"\"Starter capabilities for the {name} agent.\"\"\"

        from __future__ import annotations

        from typing import Dict

        from core.sdk import goal, step
        from core.workspace import Snapshot


        MESSAGE_KEY = "{message_key}"


        @step(inputs=["repo.root"], outputs=[MESSAGE_KEY])
        def gather_project_context(ws: Snapshot) -> Dict[str, str]:
            \"\"\"Collect a simple greeting message based on the repository root.\"\"\"
            repo_root = ws.get("repo.root", ".")
            return {{MESSAGE_KEY: f"Ready to build with Ranger (repo: {{repo_root}})"}}


        @goal(scope=[MESSAGE_KEY])
        def onboarding_complete(ws: Snapshot) -> bool:
            \"\"\"Goal is met once the greeting message exists in the workspace.\"\"\"
            return ws.exists(MESSAGE_KEY)
        """
    ).strip()
    capabilities = capabilities_template.format(name=name, message_key=f"{package}.message") + "\n"

    types_template = textwrap.dedent(
        """
        \"\"\"Dataclasses used by the {name} agent.\"\"\"

        from __future__ import annotations

        from dataclasses import dataclass, field
        from typing import Dict, List


        @dataclass
        class {class_name}Config:
            \"\"\"Example configuration object - extend to your needs.\"\"\"

            include: List[str] = field(default_factory=lambda: ["src/**/*.py"])
            metadata: Dict[str, str] = field(default_factory=dict)
        """
    ).strip()
    types = types_template.format(name=name, class_name=class_name) + "\n"

    memory_bridge_template = textwrap.dedent(
        """
        \"\"\"Helpers for persisting or retrieving atoms.

        Extend these functions when your agent needs to store additional data in memory.\"\"\"

        from __future__ import annotations

        from typing import Iterable

        from topology.types import Atom


        def persist_atoms(_: Iterable[Atom]) -> None:
            \"\"\"Placeholder helper - implement to store atoms relevant to your agent.\"\"\"
            # Example: write atoms into a dedicated table or external store.
            return None
        """
    ).strip()
    memory_bridge = memory_bridge_template + "\n"

    agent_template = textwrap.dedent(
        """
        \"\"\"Entry point for running the {name} agent.\"\"\"

        from __future__ import annotations

        from pathlib import Path

        from dotenv import load_dotenv

        from core.errors import SolveResult
        from core.sdk import Agent
        from boot import get_default_budget, setup_memory

        from . import capabilities


        load_dotenv()


        class {class_name}:
            \"\"\"Lightweight starter agent - expand capabilities to fit your domain.\"\"\"

            def __init__(self, repo_root: str | Path = ".", *, reset_registry: bool = True) -> None:
                self.repo_root = Path(repo_root).resolve()
                setup_memory(reset=reset_registry, memory_key="{package}.memory", domain="{package}")
                self._agent = Agent(
                    [capabilities.gather_project_context],
                    budget=get_default_budget(),
                )

            def run(self, *, max_steps: int = 20) -> SolveResult:
                initial_state = {{
                    "repo.root": str(self.repo_root),
                }}
                return self._agent.run(
                    initial=initial_state,
                    goal=capabilities.onboarding_complete,
                    max_steps=max_steps,
                )


        if __name__ == "__main__":
            result = {class_name}().run()
            if not result.ok:
                raise SystemExit(f"Agent blocked: {{result.blocker}}")
            print(result.final[capabilities.MESSAGE_KEY])
            print(f"Run ID: {{result.run_id}}")
        """
    ).strip()
    agent = agent_template.format(name=name, class_name=class_name, package=package) + "\n"

    test_agent_template = textwrap.dedent(
        """
        from agents.{package}.agent import {class_name}


        def test_{package}_agent_smoke(tmp_path):
            agent = {class_name}(repo_root=tmp_path, reset_registry=True)
            result = agent.run(max_steps=5)
            assert result.ok
        """
    ).strip()
    test_agent = test_agent_template.format(package=package, class_name=class_name) + "\n"

    _write_file(agent_dir / "__init__.py", __init__, force=force)
    _write_file(agent_dir / "capabilities.py", capabilities, force=force)
    _write_file(agent_dir / "types.py", types, force=force)
    _write_file(agent_dir / "memory_bridge.py", memory_bridge, force=force)
    _write_file(agent_dir / "agent.py", agent, force=force)
    _write_file(tests_dir / f"test_{package}.py", test_agent, force=force)

    return agent_dir
