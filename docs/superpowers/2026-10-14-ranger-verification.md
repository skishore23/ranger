# Ranger reliability verification — 2026-10-14

The reviewed implementation spans `98daa2c0d834368d23345547bc97c2671537de36` through `dbec82196e648cb9918c0aaa23276b08fa85b5aa` on local `main`. The user authorized fixing the reviewed defects and committing to main. This receipt adds documentation only; verified production code is unchanged. Nothing was pushed.

## Verification

| Check | Result |
| --- | --- |
| Controller fresh Python 3.13.12 full suite | 291 passed, 2 skipped; 53.65 seconds |
| Implementation Python 3.13.12 full suite | 291 passed, 2 skipped; 56.68 seconds |
| Fresh Python 3.11.12 environment/source copy | 291 passed, 2 skipped; 40.55 seconds |
| Clean wheel and editable installation | Actual installed imports and CLI outside checkout, without PYTHONPATH fallback |
| Documented workflows | Literal Hello/ReAct, guide scripts, executable scaffold, replay/diff/scenario |
| Independent review | Four task reviews, whole-change review, integration and byte-fix scoped reviews approved |
| Whitespace | Original-to-final implementation `git diff --check` clean |

The two skipped checks require optional Graphviz. CI is configured for Python 3.11–3.14; hosted CI, local 3.12/3.14 and Windows execution were not performed. All workflow tests use offline external-provider fixtures. Package installation uses an index or populated cache.

## Repaired behavior

- Owned snapshots, atomic merged validation, isolated scheduler state, fair serial scheduling, retries, final-step goals and enforced shared budgets.
- Fail-closed full-request/action guards, preserved schemas/zero settings, safe structured masking and blocked changes to reference filesystem selectors.
- Durable run evidence, read-only replay/diff, truthful scenarios, preserved history, exact facets, composite atom identity and authenticated atom policy/lineage.
- Safe whole-batch test-file validation, target/attempt/artifact SHA-256 identity, actual pytest/coverage outcomes, repairs and exact UTF-8 file bytes across newline representations.
- Effective custom memory key/domain publication and retrieval for both reference agents, all-target final-attempt scenario coverage, retrieved source identities/citations, rejected filler and explicit mandatory review.
- Deliberate packaging/dependencies, executable examples/scaffolds and accurate documentation.

Regression evidence is checked in under [core reliability](../../tests/core/test_reliability.py), [trace/storage reliability](../../tests/core/test_trace_reliability.py), [reference-agent reliability](../../tests/test_reference_agent_reliability.py), [final integration](../../tests/test_final_integration.py), and [installed workflows](../../tests/test_packaging_workflows.py). New tests failed against the corresponding prior code before repairs.

The whole-change review's four Important findings were cross-target artifact replacement, last-target-only scenario coverage, ineffective custom memory publication and lossy redacted dictionary keys. All were fixed in `f957c819f3277292b965719e86f939045bfa99de`, along with a WriteSpec documentation correction and a confirmed masking/routing-root issue. Scoped review accepted all six. Its new newline/hash regression was fixed in `dbec82196e648cb9918c0aaa23276b08fa85b5aa`; narrow re-review approved exact-byte comparison/publication and tamper checks. No required finding remains open.

## Controller decisions and their costs

1. Added deterministic execution-recency priority to prevent writer starvation. Execution order changes; downstream reference-agent and ReAct integration tests verify results.
2. Added an opt-in no-default human selection for required review. Generic form behavior could regress; existing defaults and explicit/blank approval are covered by compatibility tests.
3. Extended the final correction to block masking that changes reference filesystem selectors. Sensitive routing strings can now block actions; original destinations and ordinary content masking are tested.
4. Continued narrowly after scoped review exposed a new newline regression instead of leaving it unresolved. This added one verification cycle. Requested UTF-8 newline bytes are preserved; an LF request can rewrite an existing CRLF file, while an identical CRLF request skips unchanged bytes.

## Preserved state and limits

Earlier user edits remain untouched in stash `f56be46798d4cc2d4dee7e0442b86a07629cbe74` (`ranger-before-get-latest-2026-10-13`). The source working tree was clean after each final implementation commit.

Execution remains serial; synchronous checks cannot preempt running Python or undo external effects. Generated tests use caller privileges. Filesystem preflight/content evidence is not a sandbox, inode authentication or a transaction against arbitrary I/O failure/external races. Strict JSON tracing can be disabled explicitly. Replay reconstructs recorded state and local logs are mutable; HMAC helpers are opt-in. Research length/citation checks do not prove facts. These are explicit limits in the specification and public guides, not unresolved versions of the repaired failures.
