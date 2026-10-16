from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Set

from .workspace import Workspace, Snapshot
from .trace import Recorder, recording, emit, validate, values as snapshot_values
from .capability import Capability
from .fields import _FieldContracts
from .transitions import _TransitionChecks, input_refs, transition_record
from .errors import GoalBlocked, GoalCheck, SolveResult, WhyNot, ContractViolation
from .provenance import digest_writes
from .execution import ExecutionState, execution_scope, current_execution, guard_atoms, guard_value, selected_snapshot_scope
from topology import plan_path, summarize_regions, reconcile_overlaps, pack_context
from topology.types import Budget


def _digest_reads(cap: Capability, snap: Snapshot) -> str:
    vals = [(k, snap.get(k, None)) for k in sorted(cap.reads or set())]
    payload = json.dumps(vals, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class Engine:
    """Serial execution of stable batches with no declared read/write hazards."""
    def __init__(self, capabilities: List[Capability], budget: Optional[Budget] = None, *,
                 trace_path=".ranger/runs.db", trace: bool = False,
                 strict_contracts: bool = False, field_contracts=None, transition_checks=None):
        self.trace_domain = None
        self.trace_path = trace_path
        self.trace = trace
        if type(strict_contracts) is not bool:
            raise TypeError('strict_contracts must be a bool')
        self.strict_contracts = strict_contracts
        self._fields = _FieldContracts(field_contracts) if field_contracts is not None else None
        self._transition_checks = _TransitionChecks(transition_checks)
        self.capabilities = list(capabilities)
        self._prepare_capabilities()
        # Enough for multi-target reference workflows; limits remain finite.
        self.budget = budget if budget is not None else Budget(tokens=200000, ms=300000, calls=120)
        self._reset_run()

    def _prepare_capabilities(self):
        """Validate registrations and capture stable, independently owned keys."""
        identifiers = set()
        for cap in self.capabilities:
            if not isinstance(cap, Capability):
                raise TypeError('Every registered capability must be a Capability')
            if not isinstance(cap.id, str) or not cap.id.strip():
                raise ValueError('Capability IDs must be non-empty strings')
            if cap.id in identifiers:
                raise ValueError(f'Duplicate capability ID: {cap.id}')
            identifiers.add(cap.id)
            for attribute in ('reads', 'writes'):
                keys = getattr(cap, attribute)
                if not isinstance(keys, (set, frozenset)) or any(not isinstance(k, str) or not k for k in keys):
                    raise ValueError(f'Capability {cap.id} {attribute} must be a set of non-empty strings')
        self.capabilities = [replace(cap, reads=frozenset(cap.reads), writes=frozenset(cap.writes),
                                     tags=frozenset(cap.tags) if cap.tags is not None else None,
                                     write_specs=dict(cap.write_specs) if cap.write_specs is not None else None)
                             for cap in self.capabilities]
        if self._fields is not None:
            for cap in self.capabilities:
                self._fields.check_reads(cap.reads, cap.id)
                self._fields.authorize(sorted(cap.writes), cap.id)

    def _reset_run(self):
        self._last_read_digest: Dict[str, str] = {}
        self._last_batch_blocked = False
        self._last_blocked_cap: Optional[str] = None
        self._blocked_attempts: Dict[str, int] = {}
        self._pending: Set[str] = set()
        self._execution_recency: Dict[str, int] = {}
        self._execution_sequence = 0
        self._attempt_sequence = 0

    def solve(self, *, initial: Dict[str, Any], goal: Callable[[Snapshot], bool | GoalCheck],
              max_steps: int = 60) -> SolveResult:
        self._prepare_capabilities()
        self._reset_run()
        ws = Workspace(initial, field_contracts=self._fields.export() if self._fields is not None else None,
                       transition_checks=self._transition_checks.callbacks)
        state = ExecutionState(replace(self.budget))
        recorder = Recorder(self.trace_path, snapshot_values(ws.snapshot()), self.trace_domain, version=2) if self.trace else None
        try:
            with execution_scope(state), recording(recorder):
                try:
                    result = self._solve(ws, goal, max_steps, state)
                except BaseException as exc:
                    emit('outcome', status='failed', state=snapshot_values(ws.snapshot()),
                         error={'type': type(exc).__name__, 'message': str(exc)})
                    raise
                blocker = result.blocker
                emit('outcome', status='succeeded' if result.ok else 'blocked',
                     state=snapshot_values(result.final), steps=result.steps,
                     blocker=None if blocker is None else {
                         'reason': blocker.reason, 'missing': sorted(blocker.missing or []),
                         'details': blocker.details})
                return replace(result, run_id=recorder.run_id if recorder else None)
        finally:
            if recorder:
                recorder.close()

    def _solve(self, ws, goal, max_steps, state):
        snap = ws.snapshot()
        seen = {snap.digest()}
        stalled = False
        for step in range(max(0, max_steps) + 1):
            # Includes the initial state at zero allowance and the final batch.
            scope = getattr(goal, '__ranger_goal_scope__', set())
            try:
                missing = sorted(k for k in scope if not snap.exists(k))
                checked = False if missing else goal(snap)
                goal_met = bool(checked)
                goal_details = {'missing': missing, 'unmet': list(checked.unmet) if isinstance(checked, GoalCheck) else []}
                emit('goal', status='met' if goal_met else 'unmet', step=step,
                     name=getattr(goal, '__name__', type(goal).__name__), **goal_details)
            except GoalBlocked as blocked:
                emit('goal', status='blocked', step=step, name=getattr(goal, '__name__', type(goal).__name__), reason=blocked.reason)
                return SolveResult(False, snap, WhyNot('goal_blocked', details={
                    **blocked.details, 'reason': blocked.reason}), step)
            except Exception as exc:
                emit('goal', status='failed', step=step, name=getattr(goal, '__name__', type(goal).__name__), error=str(exc))
                raise
            if goal_met:
                # No time allowance is needed for an already-satisfied initial goal.
                if step:
                    try:
                        state.check_time()
                    except GoalBlocked as blocked:
                        return SolveResult(False, snap, WhyNot('budget', details=blocked.details), step)
                return SolveResult(True, snap, steps=step)
            stop = None
            blocker = None
            if stalled:
                stop = {'reason': 'no_progress', 'details': {}}
                blocker = WhyNot('no_progress')
            elif step >= max_steps:
                stop = {'reason': 'step_limit', 'details': {'steps': max_steps}}
                blocker = WhyNot('budget', details=stop['details'])
            else:
                try:
                    state.check_execution()
                except GoalBlocked as blocked:
                    stop = {'reason': 'budget', 'details': blocked.details}
                    blocker = WhyNot('budget', details=blocked.details)
                if stop is None:
                    exhausted = next((key for key in sorted(self._pending) if self._blocked_attempts.get(key, 0) >= 3), None)
                    if exhausted:
                        details = {'capability': exhausted, 'attempts': self._blocked_attempts[exhausted]}
                        stop = {'reason': 'retries_exhausted', 'details': details}
                        blocker = WhyNot('llm_blocked', details=details)
            ready = self._find_ready(snap)
            plan = self._pick_compatible(ready)
            schedule = self._schedule_report(snap, ready, plan, step, stop)
            emit('schedule', **schedule)
            diagnostics = {'goal': goal_details, 'schedule': schedule}
            if blocker is not None:
                return SolveResult(False, snap, replace(blocker, details={**(blocker.details or {}), **diagnostics}), step)
            if not ready:
                return SolveResult(False, snap, WhyNot('no_ready', missing=set(missing), details=diagnostics), step)
            if not plan:
                return SolveResult(False, snap, WhyNot('conflict', details={'ready': [c.id for c in ready], **diagnostics}), step)
            try:
                snap = self._apply_batch(ws, plan, snap, goal)
                state.check_time()
            except GoalBlocked as blocked:
                # Preserve earlier successful commits in a partially executed batch.
                return SolveResult(False, ws.snapshot(), WhyNot(blocked.reason, details=blocked.details), step + 1)
            digest = snap.digest()
            # Evaluate the final goal and explain the final snapshot before
            # reporting a successful no-op as no progress.
            stalled = digest in seen and not self._pending and not self._find_ready(snap)
            seen.add(digest)

    def _schedule_report(self, snap, ready, plan, step, stop):
        ready_ids = {cap.id for cap in ready}
        selected_ids = {cap.id for cap in plan}
        decisions = []
        for cap in sorted(self.capabilities, key=lambda c: c.id):
            missing = sorted(key for key in cap.reads if not snap.exists(key))
            row = {'capability': cap.id, 'status': 'idle', 'reason': 'inputs_unchanged',
                   'missing': missing, 'conflicts': [], 'attempts': self._blocked_attempts.get(cap.id, 0)}
            if missing:
                row.update(status='waiting', reason='missing_inputs')
            elif cap.id in ready_ids:
                reason = ('retry_pending' if cap.id in self._pending else
                          'never_run' if cap.id not in self._last_read_digest else 'inputs_changed')
                row.update(status='selected', reason=reason)
                if stop is not None:
                    row.update(status='blocked', reason=stop['reason'])
                elif cap.id not in selected_ids:
                    row.update(status='deferred', reason='conflict')
                    for chosen in plan:
                        keys = (cap.writes & (chosen.reads | chosen.writes)) | (cap.reads & chosen.writes)
                        if keys:
                            row['conflicts'].append({'capability': chosen.id, 'keys': sorted(keys)})
            decisions.append(row)
        return {'step': step, 'selected': [] if stop is not None else [cap.id for cap in plan],
                'capabilities': decisions, 'stop': stop}

    def _find_ready(self, snap: Snapshot) -> List[Capability]:
        ready = []
        for cap in self.capabilities:
            if any(not snap.exists(k) for k in (cap.reads or set())):
                continue
            # Successful no-ops (including SkipLLM) consume this input version.
            # Missing outputs alone do not force a retry; soft failures use pending.
            if (cap.id in self._pending
                or self._last_read_digest.get(cap.id) != _digest_reads(cap, snap)):
                ready.append(cap)
        return ready

    def _pick_compatible(self, ready: List[Capability]) -> List[Capability]:
        # Least recently attempted first: feedback writers cannot starve their readers.
        sorted_ready = sorted(ready, key=lambda c: (
            self._execution_recency.get(c.id, -1), 'compute' not in (c.tags or set()), c.id))
        plan, writes, reads = [], set(), set()
        for cap in sorted_ready:
            cap_writes, cap_reads = set(cap.writes or set()), set(cap.reads or set())
            if writes & (cap_writes | cap_reads) or reads & cap_writes:
                continue
            plan.append(cap)
            writes.update(cap_writes)
            reads.update(cap_reads)
        return plan

    def _apply_batch(self, ws: Workspace, plan: List[Capability], snap: Snapshot,
                     goal: Callable[[Snapshot], bool]) -> Snapshot:
        soft = {'llm_invalid_output', 'llm_trivial_tests', 'llm_unavailable'}
        state = current_execution()
        self._last_batch_blocked = False
        for cap in plan:
            started = time.monotonic()
            self._attempt_sequence += 1
            attempt = self._attempt_sequence
            inputs = input_refs(snap, cap.reads) if self.trace else {}
            metadata = {'attempt': attempt, 'reads': sorted(cap.reads), 'writes': sorted(cap.writes),
                        'inputs': inputs, 'checks': list(self._transition_checks.callbacks)} if self.trace else {}
            emit('capability_started', capability=cap.id, **metadata)
            writes, committed, attempted_commit = None, False, False

            def finish_metadata(status, error=None):
                if not self.trace or committed:
                    return {}
                keys = sorted(writes) if isinstance(writes, dict) and all(isinstance(k, str) and k for k in writes) else None
                checks = ws.last_transition_checks if attempted_commit else []
                source = 'transition_checks' if checks and not checks[-1]['met'] else 'execution'
                return {'transition': transition_record(
                    cap.id, attempt, inputs, cap.writes, status=status,
                    proposed_keys=keys, checks=checks,
                    error=None if error is None else {'type': type(error).__name__, 'message': str(error), 'source': source})}

            try:
                if state is not None:
                    state.start_capability()
                self._execution_recency[cap.id] = self._execution_sequence
                self._execution_sequence += 1
                rd = _digest_reads(cap, snap)
                selected = snap
                try:
                    context = None
                    if (cap.tags or set()).intersection({'action', 'llm'}):
                        context = self._build_context(cap, snap, goal)
                        entries = snap.data
                        values = guard_value({k: entry.value for k, entry in entries.items()})
                        if set(values) != set(entries):
                            raise GoalBlocked('policy_blocked', {'error': 'guard changed workspace keys'})
                        if self._fields is not None:
                            self._fields.validate_values(values, cap.id)
                        selected = Snapshot({k: replace(entry, value=values[k]) for k, entry in entries.items()})
                    if state is not None:
                        state.check_time()
                    original = snap
                    if self.strict_contracts:
                        selected = selected.with_read_contract(cap.id, cap.reads)
                        original = snap.with_read_contract(cap.id, cap.reads)
                    if self.trace:
                        inputs = input_refs(selected, cap.reads, source=snap)
                    with selected_snapshot_scope(original):
                        try:
                            writes = cap.runner.run(cap, selected, context=context)
                        finally:
                            if state is not None:
                                state.check_time()
                        if writes is None:
                            writes = {}
                        if not isinstance(writes, dict):
                            raise TypeError(f'Capability {cap.id} must return dict or None')
                        illegal = set(writes) - cap.writes
                        if illegal:
                            raise ContractViolation(cap.id, 'writes', illegal)
                        if writes and cap.post is not None:
                            if not cap.post(selected, writes):
                                raise RuntimeError(f'verify_failed: {cap.id}')
                            # Postconditions may mutate the proposed dict; check
                            # the final proposal before publishing any values.
                            illegal = set(writes) - cap.writes
                            if illegal:
                                raise ContractViolation(cap.id, 'writes', illegal)
                        if state is not None:
                            state.check_time()
                except GoalBlocked as blocked:
                    if blocked.reason not in soft:
                        raise
                    self._pending.add(cap.id)
                    self._blocked_attempts[cap.id] = self._blocked_attempts.get(cap.id, 0) + 1
                    self._last_batch_blocked = True
                    self._last_blocked_cap = cap.id
                    emit('capability_finished', capability=cap.id, status='retry',
                         duration_ms=(time.monotonic() - started) * 1000, reason=blocked.reason,
                         **finish_metadata('rejected', blocked))
                    continue
                if writes:
                    if self._fields is None:
                        validate(writes)
                    else:
                        self._fields.validate_json(writes, cap.id)
                    prov_id = f'{cap.id}:{rd}:{digest_writes(writes)}'
                    previous = ws.snapshot() if self.trace else None
                    attempted_commit = True
                    ws.cas_commit(writes, write_specs=cap.write_specs or {}, provenance_id=prov_id, writer=cap.id,
                                  before_publish=state.check_time if state is not None else None)
                    after = snapshot_values(ws.snapshot())
                    record = transition_record(cap.id, attempt, inputs, cap.writes, status='committed',
                                               previous=previous, proposed=ws.snapshot(), proposed_keys=sorted(writes),
                                               checks=ws.last_transition_checks) if self.trace else None
                    emit('commit', capability=cap.id, writes={k: after[k] for k in writes}, state=after, transition=record)
                    committed = True
                self._last_read_digest[cap.id] = rd
                self._pending.discard(cap.id)
                self._blocked_attempts.pop(cap.id, None)
            except BaseException as exc:
                emit('capability_finished', capability=cap.id,
                     status='blocked' if isinstance(exc, GoalBlocked) else 'failed',
                     duration_ms=(time.monotonic() - started) * 1000, error=str(exc),
                     **finish_metadata('rejected', exc))
                raise
            emit('capability_finished', capability=cap.id, status='succeeded',
                 duration_ms=(time.monotonic() - started) * 1000, **finish_metadata('unchanged'))
        return ws.snapshot()

    def _build_context(self, unit: Any, snapshot: Snapshot, goal: Callable[[Snapshot], bool]) -> List[Any]:
        goal_dict = {'name': getattr(goal, '__name__', 'unknown'),
                     'domain': getattr(goal, '__ranger_goal_domain__', None)}
        path = plan_path(unit=unit, goal=goal_dict, budget=self.budget)
        memory_atoms = []
        for region in path.memory_like:
            query = {'domain': region.domain} if hasattr(region, 'domain') else {}
            facets = {key: snapshot.get(key) for key in ('thread_id', 'run_id') if snapshot.exists(key)}
            if facets:
                query['facets'] = facets
            memory_atoms.extend(region.read(query))
        memory_atoms = guard_atoms(memory_atoms)
        summaries = guard_atoms(summarize_regions(path.memory_like, memory_atoms, goal_dict))
        reconciled = guard_atoms(reconcile_overlaps(summaries, path, goal_dict))
        context_window = pack_context(memory_atoms + reconciled, self.budget, goal_dict)
        return guard_atoms(context_window.atoms)

    def _missing_for(self, goal, snap) -> Set[str]:
        return {k for k in getattr(goal, '__ranger_goal_scope__', set()) if not snap.exists(k)}
