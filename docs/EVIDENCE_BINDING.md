# Bind validation to current inputs

Use `core.evidence` when a goal must require validation of the current candidate. Capture the binding **before** validation starts, attach the actual result to that same binding, and check it again when accepting the candidate.

```python
from core.sdk import Agent, goal, step
from core.evidence import capture_binding, bind_evidence, check_evidence

DEPENDENCIES = ["candidate", "criteria"]
VALIDATOR = "minimum-value@1"

@step(inputs=DEPENDENCIES, outputs=["validation"])
def validate(state):
    binding = capture_binding(state, keys=DEPENDENCIES)
    passed = state["candidate"] >= state["criteria"]["minimum"]
    return {"validation": bind_evidence(binding, validator=VALIDATOR, passed=passed)}

@goal(scope=["candidate", "criteria", "validation"])
def accepted(state):
    return check_evidence(state["validation"], state, keys=DEPENDENCIES, validator=VALIDATOR)

result = Agent([validate], strict_contracts=True, trace_path=".ranger/evidence.db").run(
    initial={"candidate": 3, "criteria": {"minimum": 2}}, goal=accepted)
assert result.ok

# A cached passing result cannot approve a replacement candidate.
changed = Agent([], trace=False).run(
    initial={"candidate": 1, "criteria": {"minimum": 2},
             "validation": result.final["validation"]}, goal=accepted)
assert not changed.ok
```

The checks return `GoalCheck`, so missing, malformed, failed and stale evidence appears as named unmet requirements in `ranger explain`. Strict capability read contracts also apply to the state keys used by these helpers.

## Files and validation context

`capture_binding(root=repo_root, files=["src/module.py", "tests/test_module.py"], context={"attempt": 2, "options": ["-q"]})` fingerprints exact file bytes with SHA-256. Files must be root-relative and resolve to regular files inside the root; duplicate canonical files are rejected. The binding includes the absolute canonical root and canonical relative paths. File size, timestamps and text newline normalization are not used as substitutes for byte identity.

Pass the application's current `root`, `files` and `context` independently to `check_evidence`. A changed file, missing file, changed file inventory, different root, changed context or different validator identifier prevents acceptance. Do not take the expected dependency list from the evidence record: a damaged record could omit a required input.

Capture before running the validator. Capturing after validation would bind a previous result to potentially different inputs. `bind_evidence` attaches a supplied Boolean result; it does not run a validator. Call `check_binding` after validation if you need to reject a changed subject before publishing either a passing or failing result.

## API

- `capture_binding(state=None, *, keys=(), root=None, files=(), context=None) -> dict`: accepts a Snapshot or mapping of JSON values and/or files. At least one key or file is required. Missing inputs and invalid scope raise. Context may contain any supported JSON value.
- `bind_evidence(binding, *, validator, passed, details=None) -> dict`: requires a non-empty validator identifier and an actual Boolean outcome. Returns a defensive JSON record; details must also be JSON.
- `check_binding(binding, state=None, *, keys=(), root=None, files=(), context=None) -> GoalCheck`: checks applicability without requiring that validation passed. Useful for retaining a current failed attempt.
- `check_evidence(evidence, state=None, *, validator, keys=(), root=None, files=(), context=None) -> GoalCheck`: additionally requires well-formed passing evidence from the expected validator.

State values use canonical strict JSON fingerprints. Changes to unrelated state do not invalidate evidence. Identical content with identical scope remains applicable; include an attempt, request ID or external version in context or selected keys when a new attempt must invalidate otherwise identical content. Bump the validator identifier when its implementation or contract changes.

Bindings cover only declared dependencies. Include policies, fixtures, relevant source dependencies and external versions when validation depends on them. These checks do not infer dependencies, automatically rerun validators, lock files, authenticate records or isolate generated code. Run validation against stable inputs; a fingerprint observes bytes at capture/check time and cannot prevent concurrent changes afterward.

## Testwriter integration

The testwriter binds pytest and coverage results to the source module, generated test files, target/attempt, coverage module, pytest arguments, execution setting and coverage threshold. It captures before pytest, checks afterward, preserves bindings in progress, and checks them again before approving terminal success. The active target must agree with the current plan, and pending write requests must match the validated file inventory and exact content. A cached success flag or legacy progress without bindings cannot approve a current candidate. Historical replay continues to describe the recorded run; it does not validate current files.
