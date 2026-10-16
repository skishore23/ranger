# State transitions and field history

Ranger records each capability attempt as a versioned transition when recording is enabled. Add `transition_checks` to enforce rules about the previous state and the complete proposed state before any of that capability's writes are published. Field contracts validate values and writers; transition checks validate how admitted state may change.

This offline example permits approval only while validation matches the current request and draft. The reviewer publishes validation and approval together, so the check must inspect the full proposal. A later edit with stale approval is rejected atomically.

```python
from core.sdk import Agent, GoalCheck, TransitionRejected, goal, step
from core.evidence import capture_binding, bind_evidence, check_evidence
from core.transitions import field_history, trajectory_run

DEPENDENCIES = ["request", "draft"]
VALIDATOR = "draft-matches-request@1"

def approval_policy(previous, proposed):
    if previous.get("request") != proposed.get("request"):
        return GoalCheck(False, ["request must remain unchanged"])
    if proposed.get("approved") is not True:
        return True
    return check_evidence(proposed.get("validation"), proposed,
                          keys=DEPENDENCIES, validator=VALIDATOR)

@step(inputs=["request"], outputs=["draft"], name="draft")
def draft(state):
    return {"draft": state["request"].strip()}

@step(inputs=DEPENDENCIES, outputs=["validation", "approved"], name="review")
def review(state):
    binding = capture_binding(state, keys=DEPENDENCIES)
    passed = state["draft"] == state["request"].strip()
    return {"validation": bind_evidence(binding, validator=VALIDATOR, passed=passed),
            "approved": passed}

@goal(scope=[*DEPENDENCIES, "validation", "approved"])
def accepted(state):
    return state["approved"] is True and check_evidence(
        state["validation"], state, keys=DEPENDENCIES, validator=VALIDATOR)

checks = {"approved-draft": approval_policy}
result = Agent([draft, review], transition_checks=checks, strict_contracts=True,
               trace_path=".ranger/transitions.db").run(
    initial={"request": "Prepare a status update", "approved": False}, goal=accepted)
assert result.ok
assert [e["value"] for e in field_history(".ranger/transitions.db", "approved",
                                          result.run_id)["entries"]] == [False, True]

@step(inputs=["draft"], outputs=["draft"], name="edit")
def edit(state):
    return {"draft": state["draft"] + " with an unreviewed addition"}

try:
    Agent([edit], transition_checks=checks, strict_contracts=True,
          trace_path=".ranger/transition_rejected.db").run(
        initial={k: e.value for k, e in result.final.data.items()}, goal=lambda state: False)
except TransitionRejected as error:
    assert error.check == "approved-draft" and error.unmet
else:
    raise AssertionError("An edit with stale approval was published")

rejected = trajectory_run(".ranger/transition_rejected.db")
assert rejected["status"] == "failed"
assert rejected["transitions"][0]["status"] == "rejected"
assert len(field_history(".ranger/transition_rejected.db", "draft")["entries"]) == 1
```

## Check contract

Supply `transition_checks={"name": callback}` to `Agent`, `Engine`, `Plan.compile`, `AgentRuntime` or `Workspace`. `None` and `{}` preserve existing admission behavior. Names must be non-empty strings and callbacks must be callable. The registration map is copied; callbacks retain their normal Python closures and external dependencies.

Callbacks receive `(previous: Snapshot, proposed: Snapshot)`. `previous` is the latest committed workspace at publication time. `proposed` includes every untouched field and every final merged write, with candidate versions and provenance. Public snapshot access exports defensive copies. Both snapshots are complete even with `strict_contracts=True`; these trusted global checks are separate from the executing capability's declared input access.

Checks run in registration order after declared write enforcement, shared field validation, merge semantics and `WriteSpec.validator`, and before the final deadline gate and publication. Return an exact Boolean or `GoalCheck`. False results, invalid return values and exceptions raise `TransitionRejected` and publish none of the proposed fields. Evaluation stops at the first failure. Exceptions become named failures containing the exception type, without its raw message. `TransitionRejected` is exported from `core.sdk`, `core.errors` and `core.contracts`, with `check`, `unmet` and `checks` attributes.

Successful empty writes skip checks and do not create field versions. Initial state is a baseline, validated by field contracts when configured; transition checks do not validate it. Keep a goal check for initial acceptance and restrict protected initial fields with [field contracts](FIELD_CONTRACTS.md). Earlier successful capability commits remain if a later attempt fails. Compatible capabilities may execute against the same selected snapshot; each publication check sees the latest accepted workspace, including earlier commits in that batch.

Checks work with recording disabled. They gate workspace writes, and cannot undo effects already performed by a tool, model call or callback. Keep checks deterministic and free of side effects. The workspace is a serial in-process boundary; this API does not add concurrent compare-and-swap or durable resume.

## Native records

New engine recordings use execution record version `2`. Each attempt has one `ranger.transition@1` record on its commit event or, when no commit occurred, its finish event. The record contains:

- A run-local `id` (`t1`, `t2`, ...), attempt number, writer capability ID and status: `committed`, `unchanged` or `rejected`.
- `inputs`: declared input field versions, provenance IDs, SHA-256 fingerprints of selected values and source workspace values. Guard-repaired values can differ from their source fingerprints.
- `changes`: accepted writes with complete `before`/`after` values, versions, provenance IDs and a `changed` flag. Unchanged and rejected attempts have no accepted changes.
- `declared_writes`, `proposed_keys`, ordered named check outcomes and an error type/message/source for rejected attempts. The source distinguishes a failed transition check from an execution failure, including a user-raised exception of the same type. `proposed_keys=None` means a valid write mapping was unavailable; the record does not store rejected proposed values.

Initial fields start at version `1`; the first write to a previously absent field also creates version `1`. Every accepted write increments that field's version, including an identical-value rewrite (`changed=False`). An empty write or rejected attempt creates no version. Versions and transition IDs are local to one run. The second run in the example starts its own baseline; it does not inherit the first run's version identities.

Inputs record declared state dependencies rather than instrumented accesses or every dependency of a model request. Bind relevant external artifacts and rules explicitly using [evidence binding](EVIDENCE_BINDING.md). Records describe accepted state movement and attempted failures; they do not establish factual accuracy or authenticate a validator.

## Read-only queries

```bash
ranger trajectory .ranger/transitions.db
ranger trajectory .ranger/transitions.db --run-id RUN_ID --json
ranger history .ranger/transitions.db approved --json
ranger history .ranger/transition_rejected.db draft --json
```

`core.transitions.trajectory_run(db_path, run_id=None)` returns `run_id`, terminal/current `status`, `record_version`, ordered `transitions` and recorded `goals`. Each transition includes the event sequence and elapsed milliseconds. `field_history(db_path, field, run_id=None)` returns the selected run and field, ordered accepted `entries`, and associated `rejections`. Entries expose value, version, writer, provenance, changed flag, input references and transition ID. A baseline has no writer or transition ID. Rejections are associated with proposed keys, or declared outputs when no valid proposal was available. An unknown field returns empty lists. Omitting the run ID selects the latest started run.

Both APIs validate evidence through `replay_run` and never import workflow code or execute callbacks, models, tools or goals. Malformed or contradictory native records raise `ValueError`. Version `1` logs remain readable: history versions are derived from accepted writes and explicitly marked `legacy=True`; native transitions and input references were not recorded and are not invented. Legacy trajectories therefore contain no native transitions.

SQLite evidence remains mutable local data. Structural validation cannot detect a consistently rewritten log. Recorder failures propagate and can leave incomplete evidence; they cannot roll back an already published write or external action. Recording and queries provide neither authenticated audit storage nor execution replay of side effects.
