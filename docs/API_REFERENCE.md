# Ranger API Reference

This quick reference summarises the core APIs you touch when building agents.

---

## Plan composition (`core.plan`)

- `action(capability, requires_initial=None, note=None)` – Wrap a decorated capability as an action node. `requires_initial` lists keys expected in the initial state; `note` adds human-readable context.
- `plan(*components)` – Compose actions (or existing plans) into a `Plan`. Supports fluent chaining via the `>>` operator.
- `Plan.actions` / `Plan.capabilities` – Ordered tuples of the actions or underlying capabilities.
- `Plan.missing_inputs` – Sequence of `(action_id, missing_keys)` that upstream actions did not satisfy.
- `Plan.initial_requirements` – Set of keys every run must seed in the initial state.
- `Plan.validate(strict=True)` – Returns missing inputs; with `strict=True` raises `GoalBlocked("plan_missing_inputs")` when wiring is incomplete.
- `Plan.describe()` – Human-readable outline of the plan, including initial requirements and unresolved inputs.
- `Plan.compile(budget=None, strict=True)` – Compile the plan into a `core.sdk.Agent`, performing validation before returning.

---

## LLM profiles (`core.llm.provider`)

- `register_llm_profile(name, *, provider=None, region_key=None, defaults=None, region_budget=None, region_factory=None)` – Declare a reusable LLM configuration. Provide either a concrete provider or a `region_key`. `defaults` may include `model`, `temperature`, `system`, `max_tokens`. `region_factory` can lazily register the region.
- `resolve_llm_profile(name)` – Returns `(provider, defaults)` and registers the region on demand if `region_factory` was supplied.
- `list_llm_profiles()` / `clear_llm_profiles()` – Introspection and reset helpers (useful in tests).

Example profile setup:

```
register_llm_profile(
    "myagent.generate",
    region_key="myagent.llm",
    defaults={"model": "gpt-4o-mini", "temperature": 0.0},
    region_factory=lambda: setup_openai_llm(key="myagent.llm", model="gpt-4o-mini", temperature=0.0),
)

@llm(profile="myagent.generate", inputs=[...], outputs=[...])
def generate(_: Snapshot):
    ...
```

---

## Decorators (`core.sdk`)

- `@step(inputs=[...], outputs=[...])` – Pure transform; return a dict with the declared outputs.
- `@tool(inputs=[...], outputs=[...])` – Side-effecting step (filesystem, HTTP, etc.).
- `@llm(profile="…", ...)` – LLM-backed tool powered by a registered profile. You may still pass explicit overrides such as `model=` or `temperature=`.
- `@human(inputs=[...], outputs=[...], fields=[...])` – Non-blocking human-in-the-loop review or approval.
- `@goal(scope=[...])` – Termination predicate; raise `GoalBlocked(reason, details={...})` for recoverable blockers.

Import these from `core.sdk` and implement each function with the signature
`fn(snapshot: Snapshot) -> dict` (or `None`).

---

## Runtime scaffold (`agents.common.runtime.AgentRuntime`)

- Constructor parameters:
  - `repo_root` (optional path, defaults to `Path.cwd()`)
  - `plan` (preferred) or `capability_list`
  - `memory_key`, `memory_domain`, `db_filename`
  - `auto_visualize`, `guard_regions`, `memory_kwargs`
- Override `build_plan()` to return a plan; the runtime will compile it once and expose it via the `plan` property.
- `run_agent(goal=..., max_steps=..., initial=None)` executes the compiled agent with standard budgeting and telemetry.
- Helpers: `build_initial_state(extra=None)`, `scenario_report(...)`, `scenario_timeline()`.

---

## LLM workflow checklist

1. Register profiles (usually inside `build_plan()`).
2. Decorate capabilities with `@llm(profile="your.profile", ...)`.
3. Run the agent—profiles resolve lazily so updated configuration is honoured.

---

## Debug aids

- `plan.validate(strict=True)` fails fast when wiring is incomplete.
- `plan.describe()` prints the action chain, initial requirements, and unresolved inputs.
- `GoalBlocked("plan_missing_inputs")` indicates the initial state (or upstream actions) must provide additional keys.

See also:

- `docs/AGENT_GUIDE.md` for a quick start walkthrough.
- `docs/README.md` for the full documentation index.
