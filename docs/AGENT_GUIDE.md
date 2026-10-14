# Build an agent

Define capabilities as small functions with declared inputs and outputs. The engine executes ready work serially and evaluates your goal before execution and after each completed batch.

```python
from core.sdk import step, goal, Agent

@step(inputs=["a"], outputs=["b"])
def increment(state):
    return {"b": state["a"] + 1}

@step(inputs=["b"], outputs=["c"])
def double(state):
    return {"c": state["b"] * 2}

@goal(scope={"c"})
def done(state):
    return state.get("c") == 4

result = Agent([increment, double], strict_contracts=True, trace_path=".ranger/demo.db").run(
    initial={"a": 1}, goal=done)
assert result.ok and result.final["c"] == 4
print(result.run_id)
```

Snapshot access via `state[key]`, `get`, `value`, `exists`, membership and `data` exports defensive values. Functions return declared writes; returning `{}` consumes the current inputs without producing outputs. Use `@step` for pure computation and `@tool` for filesystem/HTTP actions. Declarations are scheduling contracts, not a Python sandbox.

With `strict_contracts=True`, capability snapshots contain only declared inputs and explicit undeclared reads raise `ContractViolation`. Declare presence/default lookups too; seed optional inputs with None. IDs must be unique, and the engine captures independent immutable read/write key sets so caller or runner mutations cannot change the scheduled contract. Postconditions use their scoped snapshot and cannot add undeclared writes. Existing workflows can leave strict reads disabled while correcting their declarations.

After recording a run, `ranger explain .ranger/demo.db` shows why each capability ran or stayed idle; `--run-id ID --json` selects a run and returns machine-readable decisions. Boolean goals remain valid. Return `GoalCheck(met=False, unmet=["required check must pass"])` from `core.sdk` when a goal should explain its unfinished requirements.

`Agent` records strict JSON evidence automatically. Replay reconstructs state without executing tools or models. Use `ranger replay .ranger/demo.db --json` to inspect the example. Default recording is `.ranger/runs.db`; explicit `trace=False` permits non-JSON Python state. Recording errors remain visible but cannot undo external effects.

Defaults are 200000 estimated model token units, 300000 ms and 120 capability attempts; `max_steps=60` separately bounds batches. Explicit caps remain authoritative. Conservative request/completion reservations are not refunded. Synchronous deadlines cannot preempt in-flight work. See [API reference](API_REFERENCE.md) for precise limits.

To scaffold an offline starter in your project, run `ranger init demo-agent`, then `python -m pytest tests/test_demo_agent.py -q` and `python -m agents.demo_agent.agent`.

Larger agents can use `core.plan.action`/`plan` and an `agents.common.runtime.AgentRuntime` subclass. Declare seeded keys with `requires_initial`; strict plan compilation catches missing dependencies. Runtime agents use their configured memory database for execution logs and preserve existing data by default. The [provider guide](LLM_PROVIDER_GUIDE.md) supplies a complete offline profile example; the README supplies an observation-dependent ReAct loop.

The testwriter binds artifacts/results to the selected target and attempt, uses one identity-aware generator for initial and repair requests, and requires actual pytest success plus configured coverage. Missing coverage is unavailable/zero, never assumed 100%; a zero coverage target disables only the threshold. Generated Python runs with caller privileges. Full-batch path preflight rejects invalid destinations beneath `tests`, but is not a transaction against arbitrary write failures or external filesystem races.
