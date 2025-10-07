## Build an agent in 5 minutes

```python
from core.sdk import step, goal, Agent

@step(inputs=["a"], outputs=["b"])
def step1(st):
    return {"b": st["a"] + 1}

@step(inputs=["b"], outputs=["c"])
def step2(st):
    return {"c": st["b"] * 2}

@goal(scope={"c"})
def done(st):
    return st.get("c") == 4

agent = Agent([step1, step2])
result = agent.run(initial={"a": 1}, goal=done)
assert result.ok and result.final.get("c") == 4
```

- `@step`: pure transforms (no side effects) - declare inputs/outputs; return dict of writes.
- `@tool`: actions with side effects - declare inputs/outputs; return dict of writes.
- `@goal`: declare scope keys your goal depends on; return True when satisfied.
- `Agent.run`: runs fixpoint steps until goal or budget.

**Key changes from old API:**
- Use `inputs`/`outputs` instead of `reads`/`writes`
- Use `Agent.run()` instead of `Agent.solve()`
- Access state with `st["key"]` or `st.get("key")` instead of `ws.value("key")`
- `@step` for pure compute, `@tool` for actions with side effects

Advanced knobs (internal-only): `WriteSpec`, `MergeMode`, provenance on writes.
