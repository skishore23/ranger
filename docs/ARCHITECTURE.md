# Runtime boundaries

Ranger schedules declared capabilities against state. The engine coordinates execution; the workspace decides whether an update may become visible. Recording captures what happened, and read-only queries validate and reconstruct that evidence.

## Modules and dependency direction

| Layer | Responsibility | Modules |
| --- | --- | --- |
| Values | Defensive snapshots, entries and declared read views | `core.state` |
| Encoding | Strict canonical JSON and content fingerprints | `core.serialization` |
| Admission | Shared field schemas/writers and named transition checks | `core.fields`, `core.workspace` |
| Publication | Stage merged updates, run admission checks, publish atomically | `core.workspace` |
| Execution | Scheduling, runner invocation, budgets, guards and outcomes | `core.engine`, `core.execution`, `core.runners` |
| Record formats | Build and validate native transition evidence | `core.transition_records` |
| Persistence | SQLite recording, context-local event emission and read-only run selection | `core.trace` |
| Reconstruction | Event/scheduling validation and recorded state reconstruction | `core.replay` |
| Projections | Trajectory, field history and scheduling explanations | `core.transitions`, `core.explain` |
| Entry points | SDK, plans, reference-agent runtime and CLI | `core.sdk`, `core.plan`, `agents.common.runtime`, `ranger.cli` |

State values and encoding have no dependency on the engine or SQLite. Field and transition policy can therefore run with recording disabled. The record validators depend on encoding, and replay depends on record validators. Projection modules consume validated replay results. Replay does not import the SDK, providers, workspace publication or callback policy.

`core.trace` retains the recording and replay entry points. `core.workspace` retains the established `Entry`, `Snapshot` and `FieldKey` imports. Earlier transition/scheduling helper imports remain available through their original modules; implementation code imports the module that owns the responsibility.

## One update's path

1. The engine captures capability declarations and selects a compatible batch. Every capability in that batch reads the same selected source snapshot.
2. Input preparation builds action context, applies guards, validates repaired inputs and creates declared-read views when strict contracts are enabled. The original source view remains available for evidence binding.
3. The runner produces a proposal. Declared write checks run before and after a postcondition, which may modify that proposal.
4. The workspace stages every merged field, validates shared field contracts and `WriteSpec` validators, then runs transition checks against the latest previous state and complete candidate state.
5. The deadline gate runs immediately before publication. The workspace publishes the whole staged write set and returns its defensive snapshot. A rejected update preserves every previously accepted value/version, including earlier commits in the batch.
6. The engine uses that returned snapshot to record committed values and native transitions. No-op and rejected attempt records are constructed by the same record-format module.

Keep publication in `Workspace.cas_commit`. Adding a runner must not introduce a second path that bypasses field contracts, transition checks or the deadline gate. Public snapshots expose copies; they do not grant writers direct mutation of the workspace.

## Recording and reconstruction

`core.trace` reads one run and its ordered events in a read-only SQLite transaction. `core.replay.validate_run` validates the complete read in one pass, tracking event order, the active attempt, run-local field versions and terminal requirements together. A single record decoder validates each native transition's shape and its consistency with the current fields and declared inputs.

Transition construction and validation share one module and schema identifier. Runtime policy remains in the workspace admission path: named callbacks are held as a copied, validated mapping rather than a wrapper object. Reconstructing history never reruns policy callbacks. Schedule validation lives with replay and remains available through `core.explain.validate_schedule`.

Version 1 logs retain derived legacy history. Version 2 logs retain native `ranger.transition@1` records. The refactor does not change either format, fingerprint bytes, provenance IDs, input-consumption rules or goal/scheduling semantics. Scheduling/provenance digests retain their existing support for non-JSON Python state when recording is disabled; strict evidence fingerprints use the shared canonical JSON encoder.

The engine uses the retry-count map directly for retry readiness and explanations, and one attempt sequence for both transition numbering and scheduling recency. The SDK shares the provider protocol with the runner and applies default tags in one place; profile defaults are resolved by the runner when it executes.

SQLite evidence is mutable local data. Structural validation cannot authenticate a consistently rewritten log. A recorder failure can leave incomplete evidence and cannot undo an already published write or an external side effect.

## CLI and scaffolding

`ranger.cli` owns command arguments, console output and error presentation. `ranger.scaffold.create_agent_scaffold` owns generated starter files and filesystem writes, independently of Typer. Existing generated content, entry points and overwrite behavior are preserved.

## Verification

Run `python -m pytest -q` for the complete offline suite, including real wheel/editable installation and CLI workflows outside the checkout. `tests/core/test_recording_compatibility.py` additionally loads persisted v1/v2 fixtures produced at commit `0768f23`, covering accepted merged writes, no-op attempts, policy rejection, user exceptions and derived legacy history. Fixture run IDs and timing values are normalized; transition and provenance payloads are preserved.
