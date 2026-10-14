# Ranger API Reference

## State and capabilities

`Snapshot` supports `snapshot[key]`, `key in snapshot`, `get(key, default=None)`, `value(key)`, `exists(key)` and `data`. Values are defensive copies, including nested lists/dicts. `Workspace.cas_commit(writes, write_specs=..., provenance_id=...)` stages and validates the complete merged write set before publishing it. `WriteSpec` has `merge_mode`, `validator` and reserved `reducer` fields. Use `@llm(schema=...)` or an explicit validator for schema checks; validation occurs at runtime.

Import decorators from `core.sdk`:

- `@step(inputs=[...], outputs=[...])`: a pure Python transform returning declared writes.
- `@tool(inputs=[...], outputs=[...])`: an action with side effects returning declared writes.
- `@llm(provider=..., profile=..., template=..., schema=..., map=..., ...)`: model generation through the policy/budget boundary. Supply a concrete provider, region key or registered profile. Profile configuration cannot be combined with provider/region overrides; model/system/temperature/completion overrides remain supported.
- `@human(inputs=[...], outputs=[...], fields=[...])`: interactive CLI collection when stdin is a TTY; otherwise the callback receives no captured responses. Two-argument callbacks receive `(responses, snapshot)`. Select fields default a blank selection to their first option unless `default_first_option=False`; approval callbacks must validate explicitly.
- `@goal(scope=[...])`: predicate checking completion once scope keys exist. Raising `GoalBlocked(reason, details={...})` terminates the run unsuccessfully.

Capabilities returning an empty dict successfully consume the selected input digest. An LLM `map` function may raise `core.runners.llm_runner.SkipLLM` to do the same without invoking a provider. Changed input values make the capability ready again. A single-output structured LLM capability stores the **whole parsed response** under that key; multiple outputs map response keys directly to declared outputs. Supplied JSON Schema is validated before committing output, including an empty schema. Explicit `temperature=0.0` is preserved.

## Agent and budgets

`Agent(tools, *, budget=None, trace_path='.ranger/runs.db', trace=True, strict_contracts=False)` constructs the SDK runner. `run(*, initial, goal, max_steps=60)` returns `SolveResult(ok, final, blocker, steps, run_id)`. Ordinary capability exceptions are re-raised after recording a failed outcome. `WhyNot` describes an unsuccessful solve; callers should inspect `ok` before using final outputs.

IDs must be unique and non-empty within an agent. Low-level Capability input/output declarations must be sets of non-empty strings; SDK decorators also accept lists. Registration is checked before execution, and the engine captures independent immutable read/write key sets. All runners must return a dict or None and may only write declared outputs. `strict_contracts=True` additionally exposes only declared inputs to capability code, mappers, human callbacks and postconditions. An explicit undeclared read through public snapshot accessors or the exported `data` mapping raises `core.errors.ContractViolation` (a RuntimeError with `capability`, `operation`, and `keys`). Bulk `data`/`digest` access covers only declared inputs. Presence/default lookups count as reads; seed declared optional inputs with None. Goals retain full snapshot access. This is a scheduling contract, not a sandbox.

Goals may return bool or `GoalCheck(met: bool, unmet=())` from `core.sdk`/`core.errors`. `unmet` is a sequence of non-empty requirement strings; a met check cannot contain unmet requirements. Missing scope keys and named unmet requirements are recorded with goal checks. Unexplained Boolean predicates remain supported.

`topology.types.Budget(tokens=200000, ms=300000, calls=120)` specifies per-run ceilings. Those are also the default Agent budgets; explicit supplied limits take precedence. Calls count capability attempts, including retries. `max_steps` counts serial batches. Compatible capabilities read the same selected snapshot, with per-capability commits and no cross-capability read/write or write/write conflicts. Scheduling uses execution recency, compute priority and capability ID; no concurrent execution is provided.

Model tokens are estimated conservatively: serialized full-request UTF-8 bytes + 64 framing units + reserved completion limit (1500 when omitted), clamped to remaining allowance. Reservations are never refunded; custom providers must honor requested completion caps. Custom region adapters additionally account for region payloads. This is not provider billing measurement. Time checks surround synchronous work and publication; they cannot preempt in-flight work or undo side effects. An already met initial goal needs no execution allowance.

For direct custom provider calls in Python steps, use `core.execution.generate_with_policy(provider, system=..., prompt=..., model=..., temperature=..., max_tokens=..., schema=...)`. `@llm` and built-in adapters already use this boundary. Registered guards enforce policy on complete requests/action inputs, fail closed on errors/failed repairs, and reject schema-changing repairs. They do not sandbox arbitrary Python.

## Recording and replay (`core.trace`)

SDK recording defaults to `.ranger/runs.db` relative to cwd. `trace_path` changes the database; `trace=False` opts out and leaves `run_id=None`. Low-level `core.engine.Engine` defaults to nonrecording. `Plan.compile` forwards trace options. State/evidence must be strict JSON: None, exact bool/int, finite float, str, list, string-keyed dict. Tuples, sets, custom objects, non-string keys, cycles and nonfinite numbers fail visibly. Recorder errors propagate and cannot certify persistence; prior side effects or workspace publication cannot be rolled back by the recorder.

- `replay_run(db_path, run_id=None)` returns `{state, run_id, status, events}`. Omitted ID selects the latest started run. Status is `succeeded`, `blocked`, `failed`, or `running` for incomplete execution.
- `diff_runs(db_path, left_id, right_id)` returns IDs and `added`, `removed`, `changed` dictionaries; changed values carry `left`/`right`.
- `ranger replay DB --run-id ID --json` and `ranger diff DB --left ID --right ID --json` inspect recorded state.
- `core.explain.explain_run(db_path, run_id=None)` and `ranger explain DB --run-id ID --json` return recorded scheduling decisions, the last goal check, and the terminal blocker/error. Text output lists each decision and conflict/missing keys. They do not rerun code. Older logs or initially met goals have an empty schedule list.
- `ScenarioHarness.from_sqlite(db_path, domain=None, run_id=None)` and `ranger scenario DB --run-id ID --json` verify recorded outcome evidence. Missing/failed/incomplete evidence is unsuccessful. Legacy testwriter scenarios require real sufficient coverage and successful execution.

Replay is read-only state reconstruction. It never runs providers, tools, human callbacks or goal predicates. Structural validation rejects malformed records; local SQLite evidence is mutable and does not authenticate a consistently rewritten log. Atom HMAC/lineage helpers are separate opt-in mechanisms.

## Plans and runtime

- `action(capability, requires_initial=None, note=None)` wraps a capability; `requires_initial` lists seeded state keys.
- `plan(*components)` composes actions/plans, also supporting `>>`.
- `Plan.actions` / `Plan.capabilities` expose ordered tuples; `initial_requirements` exposes seeded keys; `missing_inputs` describes unsatisfied inputs.
- `Plan.validate(strict=True)` raises `GoalBlocked('plan_missing_inputs')` for incomplete wiring; `describe()` prints an outline.
- `Plan.compile(*, budget=None, strict=True, trace_path='.ranger/runs.db', trace=True, strict_contracts=False)` returns an SDK Agent. `strict` validates plan wiring; `strict_contracts` separately enforces runtime reads.

`agents.common.runtime.AgentRuntime` accepts `repo_root`, `plan` or `capability_list`, required budget/memory key/domain/database filename, and optional registry/guard/memory/visualization options plus `strict_contracts=False`. Subclasses override `build_plan()` or `build_capabilities()`. `run_agent(goal=..., max_steps=..., initial=None)` runs the compiled agent. `build_initial_state`, `scenario_report(coverage_target=None, run_id=None)` and `scenario_timeline` support inspection. The effective memory database/domain also select execution evidence. Construction and visualization preserve stored rows; `memory_kwargs={'purge': True}` deliberately purges memory atoms.

## LLM profiles (`core.llm.provider`)

`register_llm_profile(name, *, provider=None, region_key=None, defaults=None, region_budget=None, region_factory=None)` stores a provider or region configuration. Defaults may include model, temperature, system and max_tokens. Factories lazily register regions. `resolve_llm_profile(name)` returns `(provider, defaults)`; `list_llm_profiles()` and `clear_llm_profiles()` support inspection/reset. See the [offline provider guide](LLM_PROVIDER_GUIDE.md) and [agent guide](AGENT_GUIDE.md).
