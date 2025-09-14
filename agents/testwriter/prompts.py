"""Repair prompts for different failure types."""

from typing import Tuple
from .pytest_utils import FailInfo


def extract_json(response: str) -> str:
    """Extract JSON from LLM response."""
    # Try to find JSON object in response
    start = response.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    
    # Find matching closing brace
    brace_count = 0
    for i, char in enumerate(response[start:], start):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                return response[start:i+1]
    
    raise ValueError("Incomplete JSON object in response")


# System prompts for different failure types
COMPILE_SYSTEM = """You repair pytest tests for the Ranger project.
Output JSON ONLY: {"filename": "...", "code": "..."}.
Rules:
- Edit the provided test file ONLY. Do not change production code.
- Import what you need from the actual modules being tested (e.g., from core.engine.scheduler import GLOBAL_BACKOFF, DEFAULT_COOLDOWN_SECONDS).
- Also allowed: tests.helpers.kit, pytest, hypothesis, typing, pathlib, time.
- Code must compile on Python 3.11 and collect with pytest.
- Keep it deterministic; no sleeps or network.
- When applying state changes, use apply_delta(state, {"set": {...}}) ONLY; never use keys like 'add' or 'remove'.
- Use run_engine(state, contexts, is_goal=lambda s: True) with a lambda function, not is_goal=True.
- Use state.model_copy(), not state.copy() (Pydantic v2).
- Context objects have no 'events' attribute; use direct function testing instead.
- For scheduler functions: Import GLOBAL_BACKOFF and DEFAULT_COOLDOWN_SECONDS from core.engine.scheduler.
- Clean up global state in tests: Clear GLOBAL_BACKOFF entries before/after tests to avoid interference.
- For noop_commit: test apply_delta directly, not run_engine with scheduler (scheduler exits immediately if goal is met).
- For viewer functions: Edge = Tuple[str, str] (type alias), RenderOpts is a dataclass from core.observe.viewer.
- NEVER create mock classes - always import the real types from their actual modules."""

RUNTIME_SYSTEM = """You fix failing pytest tests by adjusting the TEST ONLY to correctly test the function behavior.
Do NOT modify production code. Import what you need from actual modules.
Output JSON ONLY: {"filename":"...", "code":"..."}.
Rules:
- Fix test logic while preserving the intended function testing
- Use tests.helpers.kit helpers for stable execution
- Keep function names and structure intact
- When mutating state, call apply_delta(state, {"set": {...}}) ONLY; never use keys like 'add' or 'remove'.
- Use run_engine(state, contexts, is_goal=lambda s: True) with a lambda function, not is_goal=True.
- Use state.model_copy(), not state.copy() (Pydantic v2).
- Context objects have no 'events' attribute; use direct function testing instead.
- For scheduler functions: Import GLOBAL_BACKOFF, DEFAULT_COOLDOWN_SECONDS from core.engine.scheduler.
- Clean up global state in tests to avoid interference between test runs.
- Fix assertion logic based on actual function behavior, not assumptions.
- For noop_commit: test apply_delta directly, not run_engine with scheduler (scheduler exits immediately if goal is met)."""

HYPOTHESIS_SYSTEM = """You stabilize a flaky Hypothesis test. Keep test meaning; add @settings(seed=<int>, max_examples=50, deadline=None). 
Output JSON ONLY: {"filename":"...", "code":"..."}.
Rules:
- Add hypothesis.settings decorator with seed and limits
- Constrain strategies if needed
- Keep the test property intact"""

TEMPLATE_SYSTEM = """You generate pytest test BODY ONLY for the Ranger project.
Output JSON ONLY: {"body": "    # 4-space indented test body\\n    assert ..."}.
Rules:
- Generate ONLY the function body with 4-space indentation
- Use tests.helpers.kit helpers: mk_state, mk_context, run_engine, apply_delta, NoopAction
- Keep it deterministic and under 20 lines
- No imports needed - they're provided in template
- IMPORTANT: Use apply_delta(state, {"set": {...}}) ONLY; do not use 'add' or 'remove'.
- IMPORTANT: Use run_engine(state, contexts, is_goal=lambda s: True) with a lambda function, not is_goal=True.
- IMPORTANT: Use state.model_copy(), not state.copy() (Pydantic v2).
- IMPORTANT: Context objects have no 'events' attribute; test functions directly."""


def build_repair_prompt(fail_info: FailInfo) -> Tuple[str, str]:
    """Build repair prompt based on failure type."""
    
    if fail_info["kind"] in {"compile", "collect"}:
        system = COMPILE_SYSTEM
        user = f"""FAIL_KIND: {fail_info['kind']}
FILENAME: {fail_info['filename']}
ERROR: {fail_info['trace_short']}
SNIPPET:
{fail_info['snippet']}

ALLOWED_IMPORTS:
- from tests.helpers.kit import mk_state, mk_context, NoopAction, run_engine, apply_delta
- import pytest, hypothesis, typing, pathlib, time
- Import from the actual module being tested (e.g., from core.engine.scheduler import GLOBAL_BACKOFF, DEFAULT_COOLDOWN_SECONDS)
CONSTRAINTS:
- Keep the test purpose: testing the function behavior.
- Keep function name unchanged.
- Check the actual function signature in the error trace to understand expected parameters.
- For Context objects, use mk_context() helper or create Context instances properly.
- For scheduler tests: Import global variables from the actual module, don't create local ones.
- Clean up global state: Clear GLOBAL_BACKOFF entries to avoid test interference.
- Use correct cooldown values: DEFAULT_COOLDOWN_SECONDS is 120.0, not 5.
- Fix assertion logic: should_skip_backoff returns True when time < cooldown (should skip).
Return JSON only."""

    elif fail_info["kind"] in {"assertion", "timeout"}:
        system = RUNTIME_SYSTEM
        user = f"""FAIL_KIND: {fail_info['kind']}
FUNCTION: {fail_info.get('function', 'unknown')}
NODEID: {fail_info['nodeid']}
TRACE (short):
{fail_info['trace_short']}
SNIPPET:
{fail_info['snippet']}

HINTS:
- Use tests.helpers.kit helpers for stable execution
- For quiescence tests, use run_engine(..., q=3) to lower threshold
- For delta tests, use apply_delta and check return values
- Assert on RunStats fields: ticks, steps_ok, steps_fail, steps_noop
Return JSON only."""

    else:  # hypothesis
        system = HYPOTHESIS_SYSTEM
        user = f"""FAIL_KIND: hypothesis
NODEID: {fail_info['nodeid']}
TRACE (short):
{fail_info['trace_short']}
SNIPPET:
{fail_info['snippet']}

HINTS:
- Add @hypothesis.settings(seed=42, max_examples=50, deadline=None)
- Constrain input strategies if needed
- Keep the property test meaningful
Return JSON only."""

    return system, user


