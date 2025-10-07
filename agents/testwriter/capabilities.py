"""Capabilities implementing the autonomous test writer pipeline."""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.errors import GoalBlocked
from core.sdk import goal, step, tool, llm
from core.workspace import Snapshot

from .types import TestWriterConfig, ensure_relative
from .memory_bridge import (
    store_execution_result,
    store_generated_tests,
    store_module_indexes,
)
from core.runners.llm_runner import SkipLLM

COVERAGE_PATH = ".testwriter_coverage.json"
from .utils import (
    build_plan_entries,
    candidate_test_paths,
    index_module,
    iter_candidate_sources,
    module_index_from_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_from_snapshot(ws: Snapshot) -> TestWriterConfig:
    raw = ws.get("testwriter.config", {}) or {}
    defaults = TestWriterConfig()

    include = raw.get("include")
    exclude = raw.get("exclude")

    return TestWriterConfig(
        include=list(include) if include is not None else list(defaults.include),
        exclude=list(exclude) if exclude is not None else list(defaults.exclude),
        max_attempts_per_target=int(raw.get("max_attempts_per_target", defaults.max_attempts_per_target)),
        run_tests=bool(raw.get("run_tests", defaults.run_tests)),
        pytest_args=list(raw.get("pytest_args", defaults.pytest_args)),
        coverage_target=float(raw.get("coverage_target", defaults.coverage_target)),
        verbose=bool(raw.get("verbose", defaults.verbose)),
        llm=raw.get("llm", defaults.llm),
    )


def _read_optional(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _log(config: TestWriterConfig, message: str) -> None:
    if config.verbose:
        print(message)


def _compute_target_context(ws: Snapshot) -> Optional[Dict[str, Any]]:
    entry = ws.get("tests.active.entry") or {}
    path = entry.get("path")
    if not path:
        return None

    root = Path(ws.get("repo.root", ".")).resolve()
    index_lookup = {item.get("path"): item for item in ws.get("source.index", [])}
    module_info = index_lookup.get(path)
    if not module_info:
        return None

    progress = ws.get("tests.progress", {})
    attempts = progress.get(path, {}).get("attempts", 0)

    source_path = root / path
    source_text = _read_optional(source_path) or ""
    source_lines = source_text.splitlines()

    def _extract_snippet(start: int, end: int) -> str:
        if start <= 0 or end <= 0 or not source_lines:
            return ""
        start_idx = max(start - 1, 0)
        end_idx = min(end, len(source_lines))
        return "\n".join(source_lines[start_idx:end_idx])

    existing_tests = []
    for candidate in module_info.get("existing_test_paths", []):
        content = _read_optional(root / candidate)
        existing_tests.append({"path": candidate, "content": content})

    function_snippets = []
    for fn in module_info.get("functions", []):
        start = int(fn.get("lineno", 0))
        end = int(fn.get("end_lineno", start))
        function_snippets.append({**fn, "source": _extract_snippet(start, end)})

    class_snippets = []
    for cls in module_info.get("classes", []):
        start = int(cls.get("lineno", 0))
        end = int(cls.get("end_lineno", start))
        class_snippets.append({**cls, "source": _extract_snippet(start, end)})

    return {
        "path": path,
        "module": entry.get("module"),
        "source": source_text,
        "functions": module_info.get("functions", []),
        "classes": module_info.get("classes", []),
        "function_snippets": function_snippets,
        "class_snippets": class_snippets,
        "attempt": attempts + 1,
        "existing_tests": existing_tests,
        "suggested_test_path": entry.get("suggested_test_path"),
        "reason": entry.get("reason"),
    }


# ---------------------------------------------------------------------------
# Generation helpers (must be defined before @llm declarations)
# ---------------------------------------------------------------------------


def _tests_are_trivial(payload: Optional[Dict[str, Any]], snap: Optional[Snapshot] = None) -> bool:
    if not payload:
        return True
    tests = payload.get("tests") if isinstance(payload, dict) else None
    if not tests:
        # Allow empty tests when there are no targets
        if snap and snap.get("tests.active.entry") is None:
            return False
        return True

    meaningful = sum(1 for item in tests if _is_meaningful_test(item))
    return meaningful == 0


def _is_meaningful_test(item: Any) -> bool:
    """Pure: check if single test is meaningful"""
    content = (item or {}).get("content")
    if not isinstance(content, str):
        return False
    
    lowered = content.lower()
    
    # Check for meaningful test patterns
    if any(keyword in lowered for keyword in ("pytest.raises", "monkeypatch", "from unittest.mock", "class dummy", "@pytest")):
        return True
    
    # Check for meaningful assertions
    for line in content.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not lowered.startswith("assert"):
            continue
        # Skip trivial assertions
        if any(token in lowered for token in ("hasattr", " is not none", " != none")):
            continue
        return True
    
    return False


def _format_function_list(functions: List[Dict]) -> List[str]:
    """Pure: format function metadata for prompt"""
    lines = []
    for fn in functions:
        params = ", ".join(fn.get("parameters", []))
        doc = (fn.get("docstring") or "").strip().replace("\n", " ")[:160]
        lines.append(f"- {fn.get('name')}({params}) -> {fn.get('returns')} :: {doc}")
        for hint in fn.get("hints", []):
            lines.append(f"    • {hint}")
    return lines


def _format_class_list(classes: List[Dict]) -> List[str]:
    """Pure: format class metadata for prompt"""
    lines = []
    for cls in classes:
        method_names = ", ".join(m.get("name") for m in cls.get("methods", []))
        doc = (cls.get("docstring") or "").strip().replace("\n", " ")[:160]
        lines.append(f"- {cls.get('name')} ({doc}) -> methods: {method_names}")
        for method in cls.get("methods", []):
            method_doc = (method.get("docstring") or "").strip().replace("\n", " ")[:160]
            params = ", ".join(method.get("parameters", []))
            lines.append(f"  * {method.get('name')}({params}) :: {method_doc}")
            for hint in method.get("hints", []):
                lines.append(f"      • {hint}")
    return lines


def _format_function_snippets(function_snippets: List[Dict]) -> List[str]:
    """Pure: format function source code for prompt"""
    blocks = []
    for fn in function_snippets:
        snippet = (fn.get("source") or "").strip()
        if not snippet:
            continue
        trimmed = "\n".join(snippet.splitlines()[:40])
        header = f"--- {fn.get('name')} ---"
        hint_lines = "\n".join(f"  hint: {hint}" for hint in fn.get("hints", []))
        block = f"{header}\n{trimmed}"
        if hint_lines:
            block += f"\n{hint_lines}"
        blocks.append(block)
    return blocks


def _format_class_snippets(class_snippets: List[Dict]) -> List[str]:
    """Pure: format class source code for prompt"""
    blocks = []
    for cls in class_snippets:
        snippet = (cls.get("source") or "").strip()
        if not snippet:
            continue
        trimmed = "\n".join(snippet.splitlines()[:60])
        header = f"--- {cls.get('name')} ---"
        class_hints = "\n".join(f"  hint: {hint}" for hint in cls.get("hints", []))
        block = f"{header}\n{trimmed}"
        if class_hints:
            block += f"\n{class_hints}"
        blocks.append(block)
    return blocks


def _build_generation_prompt(
    context: Dict[str, Any],
    *,
    extra_instructions: Optional[str] = None,
) -> str:
    """Orchestrate: compose generation prompt from context"""
    functions = context.get("functions", [])
    classes = context.get("classes", [])
    module = context.get("module")
    attempt = context.get("attempt", 1)
    reason = context.get("reason", "")
    source = (context.get("source") or "")[:4000]

    parts = [
        "You generate focussed pytest test files as JSON.",
        f"Target module: {module}",
        f"Attempt: {attempt}",
        f"Rationale: {reason}",
        "Desired coverage: >=50% of the module's executable lines.",
        "\nSource excerpt (trimmed):",
        source,
        "Functions:",
    ]

    parts.extend(_format_function_list(functions))

    if classes:
        parts.append("Classes:")
        parts.extend(_format_class_list(classes))

    if context.get("function_snippets"):
        parts.append("\nImportant function implementations:")
        parts.extend(_format_function_snippets(context.get("function_snippets", [])))

    if context.get("class_snippets"):
        parts.append("\nKey classes:")
        parts.extend(_format_class_snippets(context.get("class_snippets", [])))

    if context.get("existing_tests"):
        parts.append(
            "Existing tests already cover the following paths: "
            + ", ".join(test.get("path", "") for test in context["existing_tests"] if test.get("path"))
        )

    parts.append(
        textwrap.dedent(
            """
            Respond strictly in JSON with field `tests` which is an array of objects
            containing `path` (optional, relative path under tests/) and `content`
            with a complete pytest file. Generate at least three meaningful tests
            (or fewer only if the module genuinely has less behaviour). Tests must
            exercise concrete behaviour—no placeholder assertions (e.g. only
            `assert hasattr` or `assert module`). When external packages or
            network calls appear, patch them out (inject stubs into `sys.modules`,
            use `monkeypatch.setattr`, or `from unittest.mock import Mock`).
            Validate state changes, return values, and exception paths so
            regressions are detected. Every listed function/method must be covered
            by at least one assertion-driven test.

            The JSON MUST match this schema exactly—no additional keys such as
            `pytest_suite`, `tests_generated`, or language prose:

            {
              "tests": [
                {
                  "path": "tests/test_target_module.py",
                  "content": "import pytest\\nfrom target import foo\\n\\n\\ndef test_foo():\\n    assert foo() == 1\\n"
                }
              ],
              "notes": "optional commentary"
            }

            The `content` value must be a single Python string containing runnable
            pytest code (no nested JSON, no summaries). If the model cannot comply,
            return an empty array for `tests`.
            """
        ).strip()
    )

    if extra_instructions:
        parts.append("Additional constraints:\n" + extra_instructions)

    return "\n".join(parts)


def _build_repair_prompt(context: Dict[str, Any], failures: List[Dict[str, Any]]) -> str:
    failure_section = json.dumps(failures, indent=2, default=str)
    source_snippet = context.get("source", "")[:2000]
    existing_tests = context.get("existing_tests", [])
    existing_section = "\n\n".join(
        f"PATH: {test.get('path')}\n{test.get('content','')[:1000]}" for test in existing_tests if test.get("content")
    )

    return textwrap.dedent(
        f"""
        You are repairing failing pytest tests. Return JSON with an updated suite.

        TARGET MODULE: {context.get('module')}
        FAILURE DETAILS:
        {failure_section}

        MODULE SOURCE SNIPPET:
        {source_snippet}

        EXISTING TESTS:
        {existing_section or '(none)'}

        Provide revised tests that address the reported failures, improve behavioural
        coverage, and remain deterministic. Use pytest fixtures/monkeypatch to stub
        unavailable dependencies, and avoid placeholder assertions such as
        `assert result is not None` unless they prove a specific regression guard.
        The returned suite must pass when executed against the current codebase.
        """
    ).strip()


def _map_generation_prompt(snap: Snapshot) -> Dict[str, Any]:
    context = snap.get("tests.active.context")
    if not context:
        context = _compute_target_context(snap)
    if not context:
        # No targets remaining, return empty prompt to generate empty tests
        _log(_config_from_snapshot(snap), "✅ No targets remaining, generating empty tests")
        return {"prompt": "Generate empty tests: {\"tests\": []}"}

    status = snap.get("tests.active.status")
    failures = snap.get("tests.active.failures") or []
    if status == "failed" and failures:
        _log(_config_from_snapshot(snap), "⚙️ Skipping generation while repair findings exist")
        raise SkipLLM()

    prompt = _build_generation_prompt(context)
    return {"prompt": prompt}


def _map_repair_prompt(snap: Snapshot) -> Dict[str, Any]:
    status = snap.get("tests.active.status")
    failures = snap.get("tests.active.failures") or []
    if status != "failed" or not failures:
        if status != "failed":
            _log(_config_from_snapshot(snap), "ℹ️ No repair required; status is not failed")
        raise SkipLLM()

    context = snap.get("tests.active.context")
    if not context:
        context = _compute_target_context(snap)
    if not context:
        raise GoalBlocked("missing_context", details={"field": "tests.active.context"})

    prompt = _build_repair_prompt(context, failures)
    return {"prompt": prompt}


def _post_generation(snap: Snapshot, writes: Dict[str, Any]) -> bool:
    payload = writes.get("tests.active.generated")
    normalized = _normalize_generation_payload(payload, snap)
    if not normalized:
        print("   ⚠️ LLM returned invalid payload:", payload)
        entry = snap.get("tests.active.entry") or {}
        raise GoalBlocked(
            "llm_invalid_output",
            details={"path": entry.get("path")},
        )
    writes["tests.active.generated"] = normalized
    if _tests_are_trivial(normalized, snap):
        entry = snap.get("tests.active.entry") or {}
        raise GoalBlocked(
            "llm_trivial_tests",
            details={"path": entry.get("path")},
        )

    store_generated_tests(normalized.get("tests", []))
    return True


def _normalize_generation_payload(payload: Any, snap: Snapshot) -> Optional[Dict[str, Any]]:
    if payload is None:
        return None

    raw: Dict[str, Any]
    if isinstance(payload, str):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            return None
    elif isinstance(payload, dict):
        raw = deepcopy(payload)
    else:
        return None

    tests = raw.get("tests")
    if tests is None:
        structured = _extract_structured_specs(raw)
        if structured:
            tests = structured
        else:
            return None
    if isinstance(tests, dict):
        tests = [tests]
    elif isinstance(tests, str):
        try:
            parsed = json.loads(tests)
            if isinstance(parsed, list):
                tests = parsed
            elif isinstance(parsed, dict):
                tests = [parsed]
            else:
                tests = [tests]
        except json.JSONDecodeError:
            tests = [tests]

    if not isinstance(tests, list):
        return None

    entry = snap.get("tests.active.entry") or {}
    default_path = ensure_relative(entry.get("suggested_test_path") or "tests/test_generated.py")
    normalized_tests: List[Dict[str, str]] = []

    for item in tests:
        path = default_path
        content: Optional[str] = None

        if isinstance(item, str):
            content = item
        elif isinstance(item, dict):
            path = ensure_relative(item.get("path") or default_path)
            content = (
                item.get("content")
                or item.get("code")
                or item.get("body")
                or item.get("text")
                or item.get("test")
            )
        else:
            continue

        if not isinstance(content, str):
            continue

        content = _strip_code_fences(content).strip()
        if not content:
            continue

        if not content.endswith("\n"):
            content += "\n"

        normalized_tests.append({"path": path, "content": content})

    if not normalized_tests:
        structured = _extract_structured_specs(raw)
        if structured:
            rendered = _render_structured_tests(structured)
            if rendered:
                normalized_tests.append({"path": default_path, "content": rendered})

    # Allow empty tests when there are no targets
    entry = snap.get("tests.active.entry")
    if not normalized_tests and entry is None:
        # No targets remaining, empty tests are valid
        normalized_tests = []

    if not normalized_tests and entry is not None:
        return None

    normalized_payload = {
        "provider": raw.get("provider", "openai"),
        "tests": normalized_tests,
    }
    if raw.get("notes"):
        normalized_payload["notes"] = raw["notes"]

    return normalized_payload


def _strip_code_fences(text: str) -> str:
    """Pure: remove markdown code fences from text"""
    trimmed = text.strip()
    
    if not (trimmed.startswith("```") and trimmed.endswith("```")):
        return trimmed
    
    parts = trimmed.split("\n", 1)
    if len(parts) != 2:
        return trimmed.strip("`").strip()
    
    body = parts[1]
    if not body.endswith("```"):
        return trimmed.strip("`").strip()
    
    return body[:-3].strip()


def _extract_structured_specs(raw: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    suites: List[Dict[str, Any]] = []

    if "pytest_suite" in raw and isinstance(raw["pytest_suite"], dict):
        suites.append(raw["pytest_suite"])

    if "pytest_suites" in raw and isinstance(raw["pytest_suites"], list):
        suites.extend(item for item in raw["pytest_suites"] if isinstance(item, dict))

    # Some providers may wrap suites in `pytest_suite` but without list
    if not suites:
        for key, value in raw.items():
            if key.endswith("_suite") and isinstance(value, dict):
                suites.append(value)
            elif key.endswith("_suites") and isinstance(value, list):
                suites.extend(item for item in value if isinstance(item, dict))

    if not suites:
        return None

    structured_tests: List[Dict[str, Any]] = []
    for suite in suites:
        tests = suite.get("tests") if isinstance(suite, dict) else None
        if not isinstance(tests, list):
            continue
        for spec in tests:
            if isinstance(spec, dict):
                structured_tests.append(spec)

    return structured_tests or None


def _render_structured_tests(specs: List[Dict[str, Any]]) -> Optional[str]:
    if not specs:
        return None

    lines: List[str] = ["import pytest", ""]

    seen_names: set[str] = set()

    for spec in specs:
        raw_name = str(spec.get("test_name") or spec.get("name") or "generated")
        name = _slugify_test_name(raw_name, seen_names)
        seen_names.add(name)

        description = spec.get("description") or spec.get("summary")
        assertion = spec.get("assertion") or spec.get("code")
        expected = spec.get("expected_result")
        example_inputs = spec.get("input") or spec.get("inputs")

        lines.append(f"def {name}():")
        if description:
            lines.append(f"    \"\"\"{description}\"\"\"")

        if assertion:
            assertion_lines = assertion.strip().splitlines()
            for stmt in assertion_lines:
                stmt = stmt.strip()
                if not stmt:
                    continue
                if re.match(r"^def ", stmt):
                    continue
                lines.append(f"    {stmt}")
        else:
            reason_parts = []
            if expected:
                reason_parts.append(f"expected: {expected}")
            if example_inputs:
                reason_parts.append(f"inputs: {example_inputs}")
            reason = ", ".join(reason_parts) or "no assertion provided"
            lines.append(f"    pytest.skip(\"LLM provided specification only ({reason})\")")

        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    return content


def _slugify_test_name(candidate: str, taken: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", candidate).strip("_").lower() or "generated"
    if not base.startswith("test_"):
        base = f"test_{base}"

    if base not in taken:
        return base

    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


# ---------------------------------------------------------------------------
# Steps & Tools
# ---------------------------------------------------------------------------


@step(inputs=["repo.root", "testwriter.config"], outputs=["source.index"])
def discover_source_files(ws: Snapshot) -> Dict[str, Any]:
    root = Path(ws.get("repo.root", ".")).resolve()
    config = _config_from_snapshot(ws)

    indices = []
    for path in iter_candidate_sources(root, config=config):
        module_index = index_module(root, path)
        if module_index:
            indices.append(module_index.to_state())

    _log(config, f"🔍 Indexed {len(indices)} candidate source files")
    store_module_indexes(indices)
    return {"source.index": indices}


@step(inputs=["source.index", "testwriter.config"], outputs=["tests.todo", "tests.progress", "tests.summary"])
def build_test_plan(ws: Snapshot) -> Dict[str, Any]:
    index_data = ws.get("source.index", [])
    modules = [module_index_from_state(item) for item in index_data]
    root = Path(ws.get("repo.root", ".")).resolve()

    def _has_existing_tests(module: Any) -> bool:
        for candidate in candidate_test_paths(module.path):
            if (root / candidate).exists():
                return True
        return module.has_tests

    modules = [module for module in modules if not _has_existing_tests(module)]
    plan_entries = build_plan_entries(modules)
    config = _config_from_snapshot(ws)

    todo = [entry.to_state() for entry in plan_entries]
    summary = {
        "total_targets": len(todo),
        "attempted": 0,
        "passed": 0,
        "failed": 0,
    }

    _log(config, f"🗺️  Planned {len(todo)} test targets")
    return {
        "tests.todo": todo,
        "tests.progress": {},
        "tests.summary": summary,
    }


@step(inputs=["tests.todo", "tests.progress", "testwriter.config"], outputs=["tests.active.entry"])
def select_next_target(ws: Snapshot) -> Dict[str, Any]:
    todo: List[Dict[str, Any]] = ws.get("tests.todo", [])
    progress: Dict[str, Any] = ws.get("tests.progress", {})
    config = _config_from_snapshot(ws)

    for entry in todo:
        path = entry.get("path")
        metadata = progress.get(path, {})
        status = metadata.get("status")
        attempts = metadata.get("attempts", 0)
        if status == "passed":
            continue
        if attempts >= config.max_attempts_per_target and status != "passed":
            continue
        _log(config, f"🎯 Selected target {path} (attempt {attempts + 1})")
        return {"tests.active.entry": entry}

    _log(config, "✅ No remaining targets require work")
    return {"tests.active.entry": None}


@step(
    inputs=["repo.root", "tests.active.entry", "source.index", "tests.progress"],
    outputs=["tests.active.context"],
)
def gather_target_context(ws: Snapshot) -> Dict[str, Any]:
    entry = ws.get("tests.active.entry")
    if entry is None:
        # No targets remaining, return empty context
        return {"tests.active.context": None}
    
    context = _compute_target_context(ws)
    return {"tests.active.context": context}


@llm(
    profile="testwriter.generation",
    inputs=["tests.active.context", "testwriter.config"],
    outputs=["tests.active.generated"],
    template="{{ prompt }}",
    schema={
        "type": "object",
        "properties": {
            "tests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["tests"],
    },
    map=_map_generation_prompt,
    post=_post_generation,
)
def generate_test_candidates(_: Snapshot) -> None:
    """LLM-backed generator for candidate tests."""


@llm(
    profile="testwriter.generation",
    inputs=[
        "tests.active.failures",
        "tests.active.context",
        "testwriter.config",
        "tests.active.status",
    ],
    outputs=["tests.active.generated"],
    template="{{ prompt }}",
    schema={
        "type": "object",
        "properties": {
            "tests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["content"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["tests"],
    },
    map=_map_repair_prompt,
    post=_post_generation,
)
def repair_failed_tests(_: Snapshot) -> None:
    """LLM-backed repair capability for failing suites."""


@step(
    inputs=["tests.active.generated", "tests.active.entry"],
    outputs=["tests.write.requests"],
)
def prepare_write_requests(ws: Snapshot) -> Dict[str, Any]:
    entry = ws.get("tests.active.entry")
    if entry is None:
        # No targets remaining, no write requests needed
        return {"tests.write.requests": []}
    
    generated = ws.get("tests.active.generated") or {}
    tests = generated.get("tests", [])
    requests: List[Dict[str, Any]] = []
    for payload in tests:
        path = payload.get("path") or entry.get("suggested_test_path")
        if not path:
            continue
        content = payload.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        normalized = content.rstrip() + "\n"
        requests.append({"path": ensure_relative(path), "content": normalized})

    return {"tests.write.requests": requests}


@tool(inputs=["repo.root", "tests.write.requests"], outputs=["tests.write.result"])
def write_test_files(ws: Snapshot) -> Dict[str, Any]:
    root = Path(ws.get("repo.root", ".")).resolve()
    requests = ws.get("tests.write.requests", []) or []

    written: List[str] = []
    skipped: List[str] = []

    for req in requests:
        rel_path = ensure_relative(req.get("path", ""))
        if not rel_path:
            continue
        content = req.get("content", "")
        target_path = root / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        existing = _read_optional(target_path)
        if existing is not None and existing == content:
            skipped.append(rel_path)
            continue

        target_path.write_text(content, encoding="utf-8")
        written.append(rel_path)

    result = {"written": written, "skipped": skipped}
    _log(_config_from_snapshot(ws), f"💾 Wrote {len(written)} file(s), skipped {len(skipped)} unchanged")
    return {"tests.write.result": result}


@tool(
    inputs=["repo.root", "tests.write.result", "tests.write.requests", "testwriter.config"],
    outputs=["tests.run.result"],
)
def run_selected_tests(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    write_result = ws.get("tests.write.result", {})
    requests = ws.get("tests.write.requests", []) or []
    entry = ws.get("tests.active.entry") or {}

    if not config.run_tests:
        return {"tests.run.result": {"status": "skipped", "reason": "run_tests disabled"}}

    candidate_paths = set(write_result.get("written", []) + write_result.get("skipped", []))
    if not candidate_paths:
        candidate_paths = {req.get("path") for req in requests if req.get("path")}
    candidate_paths = {ensure_relative(path) for path in candidate_paths if path}

    if not candidate_paths:
        return {"tests.run.result": {"status": "skipped", "reason": "no test paths"}}

    root = Path(ws.get("repo.root", ".")).resolve()
    coverage_path = root / COVERAGE_PATH
    if coverage_path.exists():
        coverage_path.unlink()

    coverage_modules: List[str] = []
    module_name = entry.get("module")
    if isinstance(module_name, str) and module_name:
        coverage_modules.append(module_name)

    if not coverage_modules:
        coverage_modules.append("core")

    cmd = ["python", "-m", "pytest", *sorted(candidate_paths)]
    for module in coverage_modules:
        if module.startswith("--cov"):
            cmd.append(module)
        else:
            cmd.extend(["--cov", module])
    cmd.extend([
        f"--cov-report=json:{COVERAGE_PATH}",
        "--cov-report=term",
        *config.pytest_args,
    ])

    try:
        completed = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _log(config, "⏰ Pytest timed out")
        raise GoalBlocked("pytest_timeout", details={"paths": sorted(candidate_paths)})

    coverage_data = None
    if coverage_path.exists():
        try:
            coverage_data = json.loads(coverage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _log(config, f"⚠️ Unable to parse coverage report: {exc}")

    status = "passed" if completed.returncode == 0 else "failed"
    result = {
        "status": status,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "paths": sorted(candidate_paths),
        "coverage": coverage_data,
        "coverage_file": str(coverage_path) if coverage_path.exists() else None,
    }
    store_execution_result(result)
    return {"tests.run.result": result}


@step(
    inputs=["tests.run.result", "testwriter.config"],
    outputs=["coverage.report", "coverage.goal.met"],
)
def analyze_coverage(ws: Snapshot) -> Dict[str, Any]:
    run_result = ws.get("tests.run.result", {}) or {}
    coverage = run_result.get("coverage")
    config = _config_from_snapshot(ws)

    if run_result.get("status") == "skipped" and run_result.get("reason") == "run_tests disabled":
        report = {"total": 0.0, "files": {}, "target": config.coverage_target}
        return {"coverage.report": report, "coverage.goal.met": config.coverage_target <= 0.0}

    if not coverage:
        status = run_result.get("status")
        assumed_coverage = 1.0 if status == "passed" else 0.0
        report = {
            "total": assumed_coverage,
            "files": {},
            "target": config.coverage_target,
            "assumed": True,
        }
        return {
            "coverage.report": report,
            "coverage.goal.met": assumed_coverage >= config.coverage_target,
        }

    totals = coverage.get("totals", {})
    total_coverage = float(totals.get("percent_covered", 0.0)) / 100.0
    report = {
        "total": total_coverage,
        "target": config.coverage_target,
        "files": coverage.get("files", {}),
        "missing_lines": coverage.get("missing_lines", {}),
    }

    return {
        "coverage.report": report,
        "coverage.goal.met": total_coverage >= config.coverage_target,
    }


@step(
    inputs=["tests.run.result"],
    outputs=["tests.active.status", "tests.active.failures"],
)
def summarize_test_execution(ws: Snapshot) -> Dict[str, Any]:
    run_result = ws.get("tests.run.result", {}) or {}
    status = run_result.get("status", "skipped")

    failures: List[Dict[str, Any]] = []
    if status == "failed":
        failures.append(
            {
                "summary": "pytest reported failures",
                "stdout": run_result.get("stdout", ""),
                "stderr": run_result.get("stderr", ""),
            }
        )
    elif status == "error":
        failures.append(
            {
                "summary": run_result.get("error", "unknown error"),
                "stdout": run_result.get("stdout", ""),
                "stderr": run_result.get("stderr", ""),
            }
        )

    return {
        "tests.active.status": status,
        "tests.active.failures": failures,
    }


@step(
    inputs=[
        "tests.progress",
        "tests.summary",
        "tests.active.entry",
        "tests.active.status",
        "tests.active.context",
        "testwriter.config",
        "coverage.goal.met",
    ],
    outputs=["tests.progress", "tests.summary", "tests.completed", "tests.failed"],
)
def update_progress_trackers(ws: Snapshot) -> Dict[str, Any]:
    entry = ws.get("tests.active.entry")
    status = ws.get("tests.active.status")
    if not entry or not status:
        return {}

    path = entry.get("path")
    progress = dict(ws.get("tests.progress", {}))
    summary = dict(ws.get("tests.summary", {}))

    current = progress.get(path, {})
    recorded_attempts = int(current.get("attempts", 0))

    context = ws.get("tests.active.context") or {}
    target_attempt = int(context.get("attempt", 0))
    if target_attempt and recorded_attempts >= target_attempt:
        # Already recorded this attempt.
        return {}

    attempts = target_attempt if target_attempt > 0 else recorded_attempts + 1
    progress[path] = {
        "attempts": attempts,
        "status": status,
        "test_path": entry.get("suggested_test_path"),
    }

    summary["attempted"] = summary.get("attempted", 0) + 1
    if status == "passed":
        summary["passed"] = summary.get("passed", 0) + 1
    elif status in {"failed", "error"}:
        summary["failed"] = summary.get("failed", 0) + 1

    config = _config_from_snapshot(ws)
    coverage_met = ws.get("coverage.goal.met", False)
    completed = sorted(
        [
            path
            for path, meta in progress.items()
            if meta.get("status") in {"passed", "skipped"}
        ]
    )
    failed = sorted(
        [
            path
            for path, meta in progress.items()
            if meta.get("status") in {"failed", "error"}
            and meta.get("attempts", 0) >= config.max_attempts_per_target
        ]
    )

    if coverage_met and path not in completed and status == "passed":
        completed.append(path)
        completed.sort()

    return {
        "tests.progress": progress,
        "tests.summary": summary,
        "tests.completed": completed,
        "tests.failed": failed,
    }


# ---------------------------------------------------------------------------
# Terminal state detection
# ---------------------------------------------------------------------------


@step(
    inputs=["tests.todo", "tests.progress", "testwriter.config", "coverage.goal.met"],
    outputs=["tests.terminal"],
)
def detect_terminal_state(ws: Snapshot) -> Dict[str, Any]:
    todo = ws.get("tests.todo", [])
    progress: Dict[str, Any] = ws.get("tests.progress", {})
    config = _config_from_snapshot(ws)
    coverage_met = ws.get("coverage.goal.met", False)

    all_passed = True
    all_attempted = True
    pending_work = False
    for entry in todo:
        path = entry.get("path")
        metadata = progress.get(path)
        if not metadata:
            all_passed = False
            all_attempted = False
            pending_work = True
            continue

        status = metadata.get("status")
        attempts = int(metadata.get("attempts", 0))

        if status != "passed":
            all_passed = False
        if attempts < config.max_attempts_per_target:
            all_attempted = False
        if status != "passed" and attempts < config.max_attempts_per_target:
            pending_work = True

    if all_passed:
        pending_work = False


    terminal = ws.get("tests.terminal")

    failed_paths = [
        entry.get("path")
        for entry in todo
        if progress.get(entry.get("path"), {}).get("status") != "passed"
    ]

    # Success cases
    if not todo or (coverage_met and all_passed) or (config.coverage_target <= 0 and all_passed):
        desired = {"status": "success"}
    # Failure: no actionable work remains but goal unmet
    elif not pending_work:
        desired = {
            "status": "failed",
            "reason": "coverage_target_unmet" if not coverage_met else "tests_failed",
            "paths": failed_paths or [entry.get("path") for entry in todo],
            "coverage": ws.get("coverage.report"),
        }
    # Failure: attempts exhausted
    elif all_attempted:
        desired = {
            "status": "failed",
            "reason": "coverage_target_unmet" if not coverage_met else "tests_failed",
            "paths": failed_paths or [entry.get("path") for entry in todo],
            "coverage": ws.get("coverage.report"),
        }
    else:
        desired = None

    if desired is None or desired == terminal:
        return {}

    return {"tests.terminal": desired}


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------


@goal(scope=["tests.todo", "tests.progress", "testwriter.config", "coverage.goal.met", "tests.terminal"])
def tests_finished(ws: Snapshot) -> bool:
    terminal = ws.get("tests.terminal")
    if not terminal:
        return False

    status = terminal.get("status")
    if status == "failed":
        raise GoalBlocked("testwriter_failed", details={"terminal": terminal})

    return status == "success"
