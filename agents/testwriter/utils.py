"""Utility helpers for the autonomous test writer agent."""

from __future__ import annotations

import ast
import fnmatch
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .types import (
    ClassInfo,
    FunctionInfo,
    ModuleIndex,
    TestPlanEntry,
    TestWriterConfig,
    ensure_relative,
)


def module_index_from_state(data: Dict[str, Any]) -> ModuleIndex:
    """Rehydrate a :class:`ModuleIndex` from workspace state."""

    functions = [
        FunctionInfo(
            name=fn.get("name", ""),
            parameters=list(fn.get("parameters", [])),
            returns=fn.get("returns"),
            docstring=fn.get("docstring"),
            lineno=int(fn.get("lineno", 0)),
            end_lineno=int(fn.get("end_lineno", fn.get("lineno", 0))),
            is_method=bool(fn.get("is_method", False)),
            hints=list(fn.get("hints", [])),
        )
        for fn in data.get("functions", [])
    ]
    classes = []
    for cls in data.get("classes", []):
        methods = [
            FunctionInfo(
                name=method.get("name", ""),
                parameters=list(method.get("parameters", [])),
                returns=method.get("returns"),
                docstring=method.get("docstring"),
                lineno=int(method.get("lineno", 0)),
                end_lineno=int(method.get("end_lineno", method.get("lineno", 0))),
                is_method=bool(method.get("is_method", True)),
                hints=list(method.get("hints", [])),
            )
            for method in cls.get("methods", [])
        ]
        classes.append(
            ClassInfo(
                name=cls.get("name", ""),
                methods=methods,
                docstring=cls.get("docstring"),
                lineno=int(cls.get("lineno", 0)),
                end_lineno=int(cls.get("end_lineno", cls.get("lineno", 0))),
            )
        )

    return ModuleIndex(
        path=data.get("path", ""),
        module=data.get("module", ""),
        functions=functions,
        classes=classes,
        lines=data.get("lines", 0),
        has_tests=data.get("has_tests", False),
        existing_test_paths=list(data.get("existing_test_paths", [])),
        complexity=data.get("complexity", "unknown"),
        priority=float(data.get("priority", 0.0)),
    )



def iter_candidate_sources(
    root: Path,
    *,
    config: TestWriterConfig,
) -> Iterable[Path]:
    """Yield repo-relative paths to python source files to consider."""

    include = config.include
    exclude = config.exclude

    for path in root.rglob("*.py"):
        rel = ensure_relative(path.relative_to(root))
        if rel.startswith("tests/"):
            continue
        if include and not any(fnmatch.fnmatch(rel, pattern) for pattern in include):
            continue
        if any(fnmatch.fnmatch(rel, pattern) for pattern in exclude):
            continue
        yield path


def index_module(root: Path, file_path: Path) -> Optional[ModuleIndex]:
    """Build a :class:`ModuleIndex` for ``file_path`` relative to ``root``."""

    rel_path = ensure_relative(file_path.relative_to(root))
    module = rel_path[:-3].replace("/", ".")  # strip .py

    try:
        source_text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None

    functions: List[FunctionInfo] = []
    classes: List[ClassInfo] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append(_function_info(node))
        elif isinstance(node, ast.ClassDef):
            methods = [_function_info(child, is_method=True) for child in node.body if isinstance(child, ast.FunctionDef)]
            classes.append(
                ClassInfo(
                    name=node.name,
                    methods=methods,
                    docstring=ast.get_docstring(node),
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                )
            )

    total_methods = sum(len(cls.methods) for cls in classes)
    callable_count = len(functions) + total_methods
    if callable_count == 0:
        return None

    lines = source_text.count("\n") + 1
    complexity = _classify_complexity(callable_count, lines)

    suggested_test_path = suggest_test_path(rel_path)
    existing_tests = [
        candidate
        for candidate in candidate_test_paths(rel_path)
        if (root / candidate).exists()
    ]

    has_tests = len(existing_tests) > 0
    priority = _score_module(callable_count, lines, has_tests)

    return ModuleIndex(
        path=rel_path,
        module=module,
        functions=functions,
        classes=classes,
        lines=lines,
        has_tests=has_tests,
        existing_test_paths=existing_tests,
        complexity=complexity,
        priority=priority,
    )


def candidate_test_paths(module_path: str) -> List[str]:
    """Return likely test file locations for ``module_path``."""

    rel = Path(module_path)
    stem = rel.stem
    parents = rel.parent.parts

    candidates: List[str] = []

    # tests/test_<stem>.py
    candidates.append(ensure_relative(Path("tests") / f"test_{stem}.py"))

    if parents:
        # tests/<parent>/test_<stem>.py
        candidates.append(ensure_relative(Path("tests") / Path(*parents) / f"test_{stem}.py"))
        # tests/<parent>/<stem>_test.py
        candidates.append(ensure_relative(Path("tests") / Path(*parents) / f"{stem}_test.py"))
        # tests/test_<parent_join>_<stem>.py
        parent_token = "_".join(parents)
        candidates.append(ensure_relative(Path("tests") / f"test_{parent_token}_{stem}.py"))

    return sorted(set(candidates))


def suggest_test_path(module_path: str) -> str:
    """Suggest a deterministic location for tests covering ``module_path``."""

    rel = Path(module_path)
    stem = rel.stem
    parents = rel.parent.parts

    if parents:
        return ensure_relative(Path("tests") / Path(*parents) / f"test_{stem}.py")
    return ensure_relative(Path("tests") / f"test_{stem}.py")


def build_plan_entries(indices: Sequence[ModuleIndex]) -> List[TestPlanEntry]:
    """Transform module indices into ordered plan entries."""

    entries = [
        TestPlanEntry(
            path=idx.path,
            module=idx.module,
            priority=idx.priority,
            estimated_tests=max(1, len(idx.functions) + sum(len(cls.methods) for cls in idx.classes)),
            reason=_build_reason(idx),
            suggested_test_path=suggest_test_path(idx.path),
        )
        for idx in indices
    ]

    # Higher score first, tie-breaker by path for determinism
    entries.sort(key=lambda item: (-item.priority, item.path))
    return entries


def _function_info(node: ast.FunctionDef, *, is_method: bool = False) -> FunctionInfo:
    params: List[str] = []
    for arg in node.args.args:
        params.append(arg.arg)
    if node.args.vararg:
        params.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        params.append(f"**{node.args.kwarg.arg}")

    hints = _extract_function_hints(node)

    return FunctionInfo(
        name=node.name,
        parameters=params,
        returns=_safe_unparse(node.returns) if node.returns else None,
        docstring=ast.get_docstring(node),
        lineno=node.lineno,
        end_lineno=getattr(node, "end_lineno", node.lineno),
        is_method=is_method,
        hints=hints,
    )


def _safe_unparse(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    unparse = getattr(ast, "unparse", None)
    if callable(unparse):
        try:
            return unparse(node)
        except Exception:
            return None
    return None


def _classify_complexity(callables: int, lines: int) -> str:
    if callables >= 12 or lines >= 400:
        return "high"
    if callables >= 6 or lines >= 200:
        return "medium"
    return "low"


def _score_module(callables: int, lines: int, has_tests: bool) -> float:
    score = callables * 2.5
    score += min(lines / 80.0, 4.0)
    if not has_tests:
        score += 3.0
    else:
        score -= 1.5
    return round(score, 2)


def _build_reason(idx: ModuleIndex) -> str:
    functions = len(idx.functions)
    methods = sum(len(cls.methods) for cls in idx.classes)
    parts = [
        f"{functions} function{'s' if functions != 1 else ''}",
        f"{methods} method{'s' if methods != 1 else ''}",
        f"{idx.lines} lines",
        f"complexity {idx.complexity}",
    ]
    if idx.has_tests:
        parts.append("existing tests detected")
    else:
        parts.append("no tests found")
    return ", ".join(parts)


def _extract_function_hints(node: ast.FunctionDef) -> List[str]:
    """Return behavioural hints derived from raises within ``node``."""

    hints: List[str] = []

    class RaiseVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.conditions: List[str] = []

        def visit_If(self, inner: ast.If) -> None:
            condition = _safe_unparse(inner.test) or ast.dump(inner.test)
            self.conditions.append(condition)
            for stmt in inner.body:
                self.visit(stmt)
            self.conditions.pop()
            for stmt in inner.orelse:
                self.visit(stmt)

        def visit_Raise(self, inner: ast.Raise) -> None:  # noqa: N802 - ast method
            exc = inner.exc
            if isinstance(exc, ast.Call):
                exc_name = _safe_unparse(exc.func) or "Exception"
                message = exc.args[0] if exc.args else None
            else:
                exc_name = _safe_unparse(exc) or "Exception"
                message = None

            condition = " and ".join(self.conditions)
            base = f"Raises {exc_name}" if exc_name else "Raises"
            if condition:
                base += f" when {condition}"
            if message is not None:
                msg_text = _safe_unparse(message) or ""
                if msg_text:
                    base += f" (message: {msg_text})"
            hints.append(base)

    visitor = RaiseVisitor()
    visitor.visit(node)
    return hints
