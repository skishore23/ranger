from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Set

from .workspace import Workspace, Snapshot
from .capability import Capability
from .errors import GoalBlocked, SolveResult, WhyNot
from .provenance import digest_writes
from .execution import ExecutionState, execution_scope, current_execution, guard_atoms, guard_value
from topology import plan_path, summarize_regions, reconcile_overlaps, pack_context
from topology.types import Budget


def _digest_reads(cap: Capability, snap: Snapshot) -> str:
    vals = [(k, snap.get(k, None)) for k in sorted(cap.reads or set())]
    payload = json.dumps(vals, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class Engine:
    """Serial execution of stable batches with no declared read/write hazards."""
    def __init__(self, capabilities: List[Capability], budget: Optional[Budget] = None):
        self.capabilities = capabilities
        # Enough for multi-target reference workflows; limits remain finite.
        self.budget = budget if budget is not None else Budget(tokens=200000, ms=300000, calls=120)
        self._reset_run()

    def _reset_run(self):
        self._last_read_digest: Dict[str, str] = {}
        self._last_batch_blocked = False
        self._last_blocked_cap: Optional[str] = None
        self._blocked_attempts: Dict[str, int] = {}
        self._pending: Set[str] = set()

    def solve(self, *, initial: Dict[str, Any], goal: Callable[[Snapshot], bool],
              max_steps: int = 60) -> SolveResult:
        self._reset_run()
        ws = Workspace(initial)
        state = ExecutionState(replace(self.budget))
        with execution_scope(state):
            return self._solve(ws, goal, max_steps, state)

    def _solve(self, ws, goal, max_steps, state):
        snap = ws.snapshot()
        seen = {snap.digest()}
        for step in range(max(0, max_steps) + 1):
            # Includes the initial state at zero allowance and the final batch.
            scope = getattr(goal, '__ranger_goal_scope__', set())
            try:
                goal_met = not any(not snap.exists(k) for k in scope) and goal(snap)
            except GoalBlocked as blocked:
                return SolveResult(False, snap, WhyNot('goal_blocked', details={
                    **blocked.details, 'reason': blocked.reason}), step)
            if goal_met:
                # No time allowance is needed for an already-satisfied initial goal.
                if step:
                    try:
                        state.check_time()
                    except GoalBlocked as blocked:
                        return SolveResult(False, snap, WhyNot('budget', details=blocked.details), step)
                return SolveResult(True, snap, steps=step)
            if step >= max_steps:
                return SolveResult(False, snap, WhyNot('budget', details={'steps': max_steps}), step)
            try:
                state.check_execution()
            except GoalBlocked as blocked:
                return SolveResult(False, snap, WhyNot('budget', details=blocked.details), step)
            exhausted = next((key for key in self._pending if self._blocked_attempts.get(key, 0) >= 3), None)
            if exhausted:
                return SolveResult(False, snap, WhyNot('llm_blocked', details={
                    'capability': exhausted, 'attempts': self._blocked_attempts[exhausted]}), step)
            ready = self._find_ready(snap)
            if not ready:
                return SolveResult(False, snap, WhyNot('no_ready', missing=self._missing_for(goal, snap)), step)
            plan = self._pick_compatible(ready)
            if not plan:
                return SolveResult(False, snap, WhyNot('conflict', details={'ready': [c.id for c in ready]}), step)
            try:
                snap = self._apply_batch(ws, plan, snap, goal)
                state.check_time()
            except GoalBlocked as blocked:
                # Preserve earlier successful commits in a partially executed batch.
                return SolveResult(False, ws.snapshot(), WhyNot(blocked.reason, details=blocked.details), step + 1)
            digest = snap.digest()
            if digest in seen and not self._pending:
                return SolveResult(False, snap, WhyNot('no_progress'), step + 1)
            seen.add(digest)

    def _find_ready(self, snap: Snapshot) -> List[Capability]:
        ready = []
        for cap in self.capabilities:
            if any(not snap.exists(k) for k in (cap.reads or set())):
                continue
            if (cap.id in self._pending
                or any(not snap.exists(k) for k in (cap.writes or set()))
                or self._last_read_digest.get(cap.id) != _digest_reads(cap, snap)):
                ready.append(cap)
        return ready

    def _pick_compatible(self, ready: List[Capability]) -> List[Capability]:
        sorted_ready = sorted(ready, key=lambda c: ('compute' not in (c.tags or set()), -len(c.writes or set())))
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
            if state is not None:
                state.start_capability()
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
                    selected = Snapshot({k: replace(entry, value=values[k]) for k, entry in entries.items()})
                if state is not None:
                    state.check_time()
                try:
                    writes = cap.runner.run(cap, selected, context=context) or {}
                finally:
                    if state is not None:
                        state.check_time()
                if writes and cap.post is not None:
                    if not cap.post(selected, writes):
                        raise RuntimeError(f'verify_failed: {cap.id}')
                if state is not None:
                    state.check_time()
            except GoalBlocked as blocked:
                if blocked.reason not in soft:
                    raise
                self._pending.add(cap.id)
                self._blocked_attempts[cap.id] = self._blocked_attempts.get(cap.id, 0) + 1
                self._last_batch_blocked = True
                self._last_blocked_cap = cap.id
                continue
            if writes:
                prov_id = f'{cap.id}:{rd}:{digest_writes(writes)}'
                ws.cas_commit(writes, write_specs=cap.write_specs or {}, provenance_id=prov_id)
            self._last_read_digest[cap.id] = rd
            self._pending.discard(cap.id)
            self._blocked_attempts.pop(cap.id, None)
        return ws.snapshot()

    def _build_context(self, unit: Any, snapshot: Snapshot, goal: Callable[[Snapshot], bool]) -> List[Any]:
        goal_dict = {'name': getattr(goal, '__name__', 'unknown'),
                     'domain': getattr(goal, '__ranger_goal_domain__', 'unknown')}
        path = plan_path(unit=unit, goal=goal_dict, budget=self.budget)
        memory_atoms = []
        for region in path.memory_like:
            query = {'unit': getattr(unit, 'id', 'unknown'), 'goal': goal_dict['name'],
                     'facets': {'thread_id': getattr(snapshot, 'thread_id', 'unknown')}}
            memory_atoms.extend(region.read(query))
        memory_atoms = guard_atoms(memory_atoms)
        summaries = guard_atoms(summarize_regions(path.memory_like, memory_atoms, goal_dict))
        reconciled = guard_atoms(reconcile_overlaps(summaries, path, goal_dict))
        context_window = pack_context(memory_atoms + reconciled, self.budget, goal_dict)
        return guard_atoms(context_window.atoms)

    def _missing_for(self, goal, snap) -> Set[str]:
        return {k for k in getattr(goal, '__ranger_goal_scope__', set()) if not snap.exists(k)}
