# Ranger reliability implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Repair all verified review defects and commit tested changes to local main.

**Architecture:** Preserve the Python capability API while enforcing state ownership, atomic commits, isolated runs, shared budgets and policy boundaries. Add a SQLite execution recorder and read-only state replay; bind reference-agent outcomes to actual execution identity. Retain topology regions as context adapters rather than pretending they implement concurrent durable execution.

**Tech Stack:** Python 3.11+, SQLite, pytest, setuptools, Jinja2, jsonschema, injectable providers.

**Spec:** `docs/superpowers/specs/2026-10-13-ranger-reliability.md`

## Global Constraints

- Work in `/Users/kishore/ranger` on local `main`, expressly authorized by the user.
- Preserve stash `f56be46798d4cc2d4dee7e0442b86a07629cbe74` and all unrelated files. Do not push.
- No live paid API calls and no reading `.env` secrets; offline stubs at external boundaries only.
- Regression-first fixes: record the failing behavior before production edits and verify the full suite after each task.
- No new UI, agent types, concurrency framework, automatic re-execution of side effects, or general Python sandbox.
- Implementers do not spawn agents. Only the controller commits integration decisions and dispatches independent review.

## Review Focus

- Nested mutable values and merged writes: history stays unchanged and rejected commits publish no keys (Task 1).
- Guard/model failures and zero budgets: no protected outward call occurs, including providers invoked by a Python step (Task 1).
- Existing databases and malformed/truncated execution records: inspection preserves data and replay refuses false success (Task 2).
- Two targets and repair attempts: only matching completed execution can change progress (Task 3).
- Fresh installation outside the checkout and literal documented examples: imports, CLI and examples execute successfully (Task 4).

### Task 1: State, scheduler, execution budgets, guards and structured models

**Files:** `core/workspace.py`, `core/engine.py`, `core/sdk.py`, `core/runners/llm_runner.py`, `core/llm/provider.py`, `regions/llm_openai.py`, `regions/guard_pii.py`, `topology/stitch.py`, `topology/types.py`, focused new `core/` helpers as needed; tests under `tests/core/`, `tests/topology/`.

**Interfaces:** Preserve `Agent.run(initial=..., goal=..., max_steps=...)`, `Workspace.snapshot()`, `Snapshot` values, and `Budget(tokens, ms, calls)`. Expose a context-local execution budget/policy mechanism for provider calls from sample Python steps. Later tasks may add recorder callbacks to Engine without replacing its scheduling behavior. Calls count capability invocations; prompt/output tokens share a conservative estimated per-run allowance. Defaults must support the reference agents once enforcement exists; adjust documented defaults deliberately rather than silently bypass limits.

- [x] Write regressions for nested ownership, atomic validation and merged validation, repeated Agent calls, zero/final steps, refresh retry, read/write hazards, zero calls/time/tokens, shared token usage, guard exception/block/mask/summary paths, schemas and zero temperature. Core expectations include:

```python
ws = Workspace({'nested': {'items': [1]}})
old = ws.snapshot()
old.get('nested')['items'].append(2)
assert ws.snapshot().get('nested') == {'items': [1]}
assert old.get('nested') == {'items': [1]}

def reject(value):
    raise ValueError('rejected')
with pytest.raises(ValueError):
    ws.cas_commit({'first': 1, 'second': 1},
                  write_specs={'first': WriteSpec(), 'second': WriteSpec(validator=reject)},
                  provenance_id='test')
assert not ws.snapshot().exists('first')
```

- [x] Run regressions and record expected failures before implementation.
- [x] Implement defensive copies/read-only owned storage, stage validated merged entries before publication, reset run state, stable hazard-aware batches, goal checks around batches, and pending retries after soft failures. Implement per-run limits and guarded outbound requests with fail-closed validation. Validate schemas before writes; preserve zero settings. Document any unavoidable synchronous timing/token-estimate limitations in the report for Task 4.
- [x] Run focused and complete pytest suites. Do not hide baseline failures; report and resolve integration failures within this scope.
- [x] Commit task-owned production and regression files to main with `fix(core): enforce state, execution and policy invariants`; report commit SHA and actual red/green evidence.

### Task 2: Durable evidence, truthful scenario verification and safe storage

**Files:** `core/` execution recorder module, `core/engine.py`, `core/sdk.py`, `core/errors.py` if result metadata needs extending, `agents/common/runtime.py`, `ranger/scenario.py`, `ranger/cli.py`, `regions/mem_sqlite.py`, `topology/attest.py`, tests for replay/storage/CLI.

**Interfaces:** Extend Agent construction with `trace_path` (default `.ranger/runs.db`) and `trace=False` to disable recording. Extend SolveResult with optional `run_id` without breaking positional fields. Runtime agents record to their existing database. Create `core.trace.replay_run(db_path, run_id=None) -> dict` with `state`, `run_id`, `status`, and ordered `events`, and `core.trace.diff_runs(db_path, left_id, right_id) -> dict` with added/removed/changed final-state keys. CLI commands `replay` and `diff` consume these APIs; legacy scenario APIs remain available and gain truthful outcomes. Replay applies recorded snapshots/writes only; it does not run registered capabilities.

- [x] Write regressions for automatic successful/blocked/failed traces, two runs in one database, run selection, replayed final values, state difference, recording failure, malformed events, unsupported values, legacy failed-high-coverage and missing coverage, reconstruction/visualization preserving prior rows, exact JSON thread filters, shared-domain identical IDs and migration, signed policy/lineage changes and deterministic summaries. Acceptance example:

```python
from core.trace import replay_run
result = Agent([inc], trace_path=tmp_path / 'runs.db').run(
    initial={'a': 1}, goal=done, max_steps=1)
assert result.ok
assert result.run_id
assert replay_run(tmp_path / 'runs.db', result.run_id)['state'] == {'a': 1, 'b': 2}
assert ScenarioHarness(failed_execution_atoms).generate_report(coverage_target=.8).ok is False
```

- [x] Run new tests against the prior state and record expected failures.
- [x] Implement ordered durable event recording with explicit serialization and run outcomes; implement read-only replay/diff and truthful scenarios. Default purge off. Remove phantom metadata query filters; match JSON facets exactly; migrate atom keys without losing rows; derive summary IDs from content. Sign security-relevant fields and enforce chain metadata.
- [x] Repair the Task 1 integration regression in hazard-aware selection: a self-updating `a.loop` writing `n` repeatedly must not starve a ready `b.consume` reading `n` and writing `done`. Record a failing public-Agent regression, then add deterministic per-run execution-recency priority before static compute/ID tie-breakers. Check reversed registration order, reset across runs and a model/action reader following a compute writer.
- [x] Run focused and full pytest suites, including existing CLI/agent tests. Verify replay/inspection has no source/provider side effects.
- [x] Commit owned changes with `feat(trace): persist and verify execution evidence safely`; report APIs, schema/migration choices, SHA and test evidence.

### Task 3: Reference agent file safety, result identity and research integrity

**Files:** `agents/testwriter/`, `agents/deep_research/`, their tests, and `boot.py` only for explicit reference-agent budget defaults if Task 1 did not own it. Use Task 1 budget/policy integration; do not fork a second execution mechanism.

**Interfaces:** Keep `TestWriterAgent.run()` and `DeepResearchAgent.run()` usable. Bind target/attempt IDs across every intermediate artifact/result. Preserve generation provider injection for offline tests. Test writes are constrained to resolved `repo_root/tests`. Research citations reference retrieved source identities/URLs, and length checks reject rather than manufacture content.

- [x] Add failing integration regressions with real temporary files/pytest: traversal/absolute/source/symlink paths; whole-batch path validation; two modules with the second generated assertion wrong; one repaired target; absent/low coverage; documented Firecrawl response; required human-feedback construction and waiting; source-aware draft prompts and invented/duplicate citations; too-short/repeated-filler reports. Testwriter acceptance requires:

```python
assert (root / 'tests/test_mod1.py').exists()
assert result.final.get('tests.progress')['pkg/mod1.py']['status'] != 'passed'
assert result.ok is False  # the second actual test fails
assert outside.read_text() == 'original'
```

- [x] Observe expected regression failures before implementation.
- [x] Validate normalized/resolved test paths before file writes. Tie current status to a completed matching run/attempt; invalidate stale artifacts on target transitions, and ensure terminal checks cannot use stale identities. Do not treat absent coverage as 100%. Correct Firecrawl contract, human initial requirements, citation prompts/link validation and honest content-quality checks.
- [x] Run focused and full pytest suites with deterministic external stubs, including public-agent end-to-end calls. Replace blocked-run skip tests with offline assertions; do not weaken assertions to accommodate failures.
- [x] Commit owned changes with `fix(agents): bind outcomes to verified safe execution`; report SHA, red/green results and remaining limitations.

### Task 4: Package installation, executable docs and continuous verification

**Files:** `pyproject.toml`, `README.md`, affected public docs and architecture files, `ranger/cli.py` scaffold templates, `.github/workflows/`, `tests/` package/example/scaffold regressions. Reconcile Task 1–3 implementation reports before documenting API details.

**Interfaces:** Package explicitly includes `core*`, `ranger*`, `agents*`, `regions*`, `topology*` plus `boot` module. Jinja2 and jsonschema are declared dependencies. README Hello uses supported Snapshot access and explicit demo trace path. ReAct loops depend on actual observations, extract the thought value correctly, and gate answer production. Public examples are offline executable fixtures rather than untested prose.

- [x] Add failing tests for installed imports/CLI away from source, generated scaffold execution, literal Hello and dependency-gated ReAct examples, and package metadata/dependencies via actual build/install behavior. Avoid config-text assertions.
- [x] Run the fresh build/install and examples before changes and record actual failures.
- [x] Add explicit setuptools build discovery and dependencies; rewrite stale claims around serial scheduling, estimated token budgets, synchronous deadlines, run recording and read-only replay. Update research architecture to remove filler claims. Add offline CI for supported Python versions and clean wheel/editable installation.
- [x] Run full pytest, clean wheel and editable installs, imports/CLI outside checkout, documented Hello/ReAct/scaffold/replay/diff commands, and diff whitespace checks. Confirm main branch and stash identity before final commit.
- [x] Commit verified package/docs/CI changes with `fix(packaging): make installation and documented workflows executable`. Include this spec and plan in that documentation commit. Final independent review covers the entire original-head-to-final-head change.

## Completion evidence

All four tasks and final integration corrections are committed through `dbec82196e648cb9918c0aaa23276b08fa85b5aa`. Final verification passes 291 tests with 2 optional Graphviz skips on Python 3.13 and fresh Python 3.11, including clean wheel/editable installs outside the checkout. The controller independently reran the final suite: 291 passed, 2 skipped. CI is configured for Python 3.11–3.14; hosted CI and local 3.12/3.14 execution were not performed. See the [verification receipt](../2026-10-14-ranger-verification.md) for findings, evidence and implementation decisions.

- [x] Controller independent review of the entire original-head-to-final-head change, with findings resolved and scoped re-reviewed. All required findings are addressed; no Critical/Important finding remains open.
