"""Type definitions for the autonomous test writer agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FunctionInfo:
    """Structured metadata about a function or method in a module."""

    name: str
    parameters: List[str] = field(default_factory=list)
    returns: Optional[str] = None
    docstring: Optional[str] = None
    lineno: int = 0
    end_lineno: int = 0
    is_method: bool = False
    hints: List[str] = field(default_factory=list)

    def to_state(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClassInfo:
    """Structured metadata about a class in a module."""

    name: str
    methods: List[FunctionInfo] = field(default_factory=list)
    docstring: Optional[str] = None
    lineno: int = 0
    end_lineno: int = 0

    def to_state(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["methods"] = [method.to_state() for method in self.methods]
        return payload


@dataclass(frozen=True)
class ModuleIndex:
    """Information collected for a single Python module."""

    path: str  # repo-relative path
    module: str  # import path
    functions: List[FunctionInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    lines: int = 0
    has_tests: bool = False
    existing_test_paths: List[str] = field(default_factory=list)
    complexity: str = "low"
    priority: float = 0.0

    def to_state(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["functions"] = [fn.to_state() for fn in self.functions]
        payload["classes"] = [cls.to_state() for cls in self.classes]
        return payload


@dataclass(frozen=True)
class TestPlanEntry:
    """Single unit of work for the agent."""

    path: str
    module: str
    priority: float
    estimated_tests: int
    reason: str
    suggested_test_path: str

    def to_state(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TestWriterConfig:
    """Runtime configuration for the test writer agent."""

    include: List[str] = field(default_factory=lambda: ["core/**/*.py"])
    exclude: List[str] = field(
        default_factory=lambda: [
            "tests/**",
            "**/__pycache__/**",
            ".venv/**",
            "**/.venv/**",
            "env/**",
            "**/site-packages/**",
        ]
    )
    max_attempts_per_target: int = 2
    run_tests: bool = True
    pytest_args: List[str] = field(default_factory=lambda: ["-q"])
    coverage_target: float = 0.5
    verbose: bool = False
    llm: Optional[Dict[str, Any]] = field(
        default_factory=lambda: {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.0,
        }
    )

    def to_state(self) -> Dict[str, Any]:
        return asdict(self)


def ensure_relative(path: str | Path) -> str:
    """Return the given path as a POSIX-style relative string."""

    if isinstance(path, Path):
        rel = path.as_posix()
    else:
        rel = path.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return rel
