# Shared field contracts

Pass `field_contracts` to `Agent`, `Engine`, `Plan.compile`, `AgentRuntime` or `Workspace` to define the workspace's complete field set, JSON Schemas and permitted writers. `None` disables this feature and preserves existing behavior. Supplying `{}` enables it and permits no fields. It is independent of `strict_contracts`, which governs capability reads.

```python
from core.sdk import Agent, FieldContract, FieldContractViolation, goal, step
from core.evidence import capture_binding, bind_evidence, check_evidence

DEPENDENCIES = ["request", "draft"]
VALIDATOR = "draft-matches-request@1"
FIELDS = {
    "request": FieldContract(schema={"type": "string", "minLength": 1}, allow_initial=True),
    "draft": FieldContract(schema={"type": "string", "minLength": 1},
                           allowed_writers=["draft"]),
    "validation": FieldContract(
        schema={"type": "object", "required": ["passed", "binding"],
                "properties": {"passed": {"type": "boolean"}, "binding": {"type": "object"}}},
        allowed_writers=["verify"]),
}

@step(inputs=["request"], outputs=["draft"], name="draft")
def draft(state):
    return {"draft": state["request"].strip()}

@step(inputs=DEPENDENCIES, outputs=["validation"], name="verify")
def verify(state):
    binding = capture_binding(state, keys=DEPENDENCIES)
    passed = state["draft"] == state["request"].strip()
    return {"validation": bind_evidence(binding, validator=VALIDATOR, passed=passed)}

@goal(scope=[*DEPENDENCIES, "validation"])
def accepted(state):
    return check_evidence(state["validation"], state, keys=DEPENDENCIES, validator=VALIDATOR)

agent = Agent([draft, verify], field_contracts=FIELDS, strict_contracts=True,
              trace_path=".ranger/fields.db")
result = agent.run(initial={"request": "Prepare a status update"}, goal=accepted)
assert result.ok

# Initial state is checked before the goal, even if a supplied flag could satisfy it.
try:
    agent.run(initial={"request": 42}, goal=lambda state: True)
except FieldContractViolation as error:
    assert error.field == "request" and error.reason == "schema"
else:
    raise AssertionError("Invalid initial state was accepted")

try:
    agent.run(initial={"validation": result.final["validation"]}, goal=lambda state: True)
except FieldContractViolation as error:
    assert error.field == "validation" and error.reason == "initial_not_allowed"
else:
    raise AssertionError("A protected field was seeded")
```

## Field definition

`FieldContract(schema, allowed_writers=(), allow_initial=False)` accepts a JSON Schema object or Boolean schema. `allowed_writers` is a collection of exact capability IDs, including explicit decorator `name=` IDs. An empty collection permits no capability updates. `allow_initial=True` separately permits the caller to seed that field; a capability named `init` does not grant initialization permission. Contracts and their schemas are defensively captured by the engine/workspace, so mutating the supplied map or definitions does not change a running policy.

Every declared capability input/output must exist in the supplied map. Disallowed declared writers are rejected at registration and rechecked before each run. Initial values must be a mapping, contain only known initial-eligible fields, and satisfy the same schemas as updates. Fields are optional until present; no values are inserted automatically. Declare required presence using capability inputs or goal scope. Seed optional inputs with `None` only when their schema permits null.

Values are strict JSON even with `trace=False`: null, Boolean, integer, finite number, string, list and string-keyed object. Schemas support nested constraints and local references; the default draft is 2020-12, or a supported draft named by `$schema`. Validation never retrieves remote schemas; unresolved references fail closed. JSON Schema `format` remains an annotation, matching jsonschema's default behavior; use structural constraints or an explicit domain validator for additional checks.

## Publication and errors

Proposed values must be strict JSON before provenance hashing or merging. Shared schemas validate the final merged value after `SET`/`MERGE_JSON` semantics. Capability-level `WriteSpec.validator` checks still run and cannot weaken the shared schema. All proposed values must pass before any value in that capability's update is published. Earlier successful capability commits remain if a later one fails. Python, tool, LLM, human and custom runners use the same publication boundary, including changes made by postconditions. Guard-repaired selected inputs must also satisfy the shared schemas before runner execution; they are transient inputs, not committed writes.

Standalone `Workspace.cas_commit(..., writer="capability-id", provenance_id="...")` requires an explicitly allowed writer when contracts are enabled. Writer authority is not parsed from a provenance string. Engine calls supply the executing capability ID.

`FieldContractViolation` is a RuntimeError exported by `core.sdk`, `core.errors` and `core.contracts`. It carries `field`, `writer`, `reason` and `path`. Reasons are `unknown_field`, `initial_not_allowed`, `writer_not_allowed` and `schema`; schema errors identify a nested instance path when available. Diagnostics omit raw rejected values. Invalid definitions/containers raise TypeError or ValueError. Invalid initial state is rejected before execution recording begins; update failures are recorded with the last accepted state.

## Scope and authority

Writer permission applies to an entire workspace key, including every nested value. Separate model-editable `draft` and trusted `approval` keys instead of putting both inside one model-writable object. Schema validity does not establish factual accuracy. The application controls trusted capability registration, initial inputs and reviewer authentication; these contracts do not authenticate callers, sandbox Python, enforce reader access control or provide durable approval waits.

Field contracts govern admission. [Evidence binding](EVIDENCE_BINDING.md) governs whether a validation result still applies. Use both when approval depends on an exact candidate, and bind the relevant policy version as context when changes to validation rules should invalidate earlier evidence. Historical replay remains reconstruction of recorded state; it does not rerun today's contracts.
