from __future__ import annotations
from typing import List, Dict, Any, Callable, Tuple, Set, Optional
from .workspace import Workspace, Snapshot
from .capability import Capability
from .errors import GoalBlocked, SolveResult, WhyNot
import hashlib
import json
from .provenance import digest_writes

# Topology imports
from topology import (
    plan_path,
    summarize_regions,
    reconcile_overlaps,
    repair_context,
    pack_context,
)
from topology.types import Budget


def _digest_reads(cap: Capability, snap: Snapshot) -> str:
    keys = sorted(list(cap.reads or set()))
    vals = [(k, snap.get(k, None)) for k in keys]
    payload = json.dumps(vals, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Engine:
    def __init__(self, capabilities: List[Capability], budget: Optional[Budget] = None):
        self.capabilities = capabilities
        self._last_read_digest: Dict[str, str] = {}
        self.budget = budget or Budget(tokens=12000, ms=60000, calls=12)
        self._last_batch_blocked = False
        self._last_blocked_cap: Optional[str] = None
        self._blocked_attempts: Dict[str, int] = {}

    def solve(
        self,
        *,
        initial: Dict[str, Any],
        goal: Callable[[Snapshot], bool],
        max_steps: int = 60,
    ) -> SolveResult:
        ws = Workspace(initial)
        snap = ws.snapshot()
        seen: Set[str] = {snap.digest()}
        
        print(f"\n🔄 Engine starting with {len(self.capabilities)} capabilities, max_steps={max_steps}")
        print(f"   Initial state: {list(snap.data.keys())}")
        
        for step in range(max_steps):
            print(f"\n📍 Step {step + 1}/{max_steps}")
            
            # Check goal
            scope = getattr(goal, "__ranger_goal_scope__", set())
            goal_blocked_by_scope = scope and any(not snap.exists(k) for k in scope)
            try:
                goal_met = False if goal_blocked_by_scope else goal(snap)
            except GoalBlocked as blocked:
                print(f"   Goal blocked: {blocked.reason}")
                details = dict(blocked.details) if blocked.details else {}
                details.setdefault("reason", blocked.reason)
                return SolveResult(
                    ok=False,
                    final=snap,
                    blocker=WhyNot("goal_blocked", details=details),
                    steps=step,
                )
            
            if goal_blocked_by_scope:
                missing_scope = [k for k in scope if not snap.exists(k)]
                print(f"   Goal scope missing: {missing_scope}")
            else:
                print(f"   Goal check: {'✅ MET' if goal_met else '❌ NOT MET'}")
                if goal_met:
                    print(f"🎉 Goal achieved in {step} steps!")
                    return SolveResult(ok=True, final=snap, steps=step)
            
            # Find ready capabilities
            ready = self._find_ready(snap)
            print(f"   Ready capabilities: {len(ready)}")
            for cap in ready:
                reads_status = [f"{k}:{'✓' if snap.exists(k) else '✗'}" for k in (cap.reads or set())]
                writes_status = [f"{k}:{'✓' if snap.exists(k) else '✗'}" for k in (cap.writes or set())]
                print(f"     - {cap.id} (reads: {reads_status}, writes: {writes_status})")
            
            if not ready:
                print("❌ No ready capabilities found!")
                missing = self._missing_for(goal, snap)
                print(f"   Missing for goal: {missing}")
                
                # Debug: show why each capability isn't ready
                for cap in self.capabilities:
                    missing_reads = [k for k in (cap.reads or set()) if not snap.exists(k)]
                    existing_writes = [k for k in (cap.writes or set()) if snap.exists(k)]
                    read_digest = _digest_reads(cap, snap)
                    last_digest = self._last_read_digest.get(cap.id)
                    reads_changed = last_digest != read_digest
                    
                    if missing_reads:
                        print(f"     {cap.id}: missing reads {missing_reads}")
                    elif existing_writes and not reads_changed:
                        print(f"     {cap.id}: writes exist {existing_writes}, reads unchanged")
                    else:
                        print(f"     {cap.id}: should be ready? (debug needed)")
                
                return SolveResult(ok=False, final=snap, blocker=WhyNot("no_ready", missing=missing), steps=step)
            
            # Pick compatible batch
            plan = self._pick_compatible(ready)
            if not plan:
                print("❌ No compatible capabilities found!")
                return SolveResult(ok=False, final=snap, blocker=WhyNot("conflict", details={"ready": [c.id for c in ready]}), steps=step)
            
            print(f"   Executing batch: {[cap.id for cap in plan]}")
            
            # Apply batch
            old_digest = snap.digest()
            snap = self._apply_batch(ws, plan, snap, goal)
            new_digest = snap.digest()

            print(f"   State after: {list(snap.data.keys())}")
            print(f"   Digest: {old_digest[:8]} → {new_digest[:8]}")

            if new_digest in seen:
                if self._last_batch_blocked and self._last_blocked_cap:
                    attempts = self._blocked_attempts.get(self._last_blocked_cap, 0)
                    print(
                        f"⚠️ Batch blocked by {self._last_blocked_cap} (attempt {attempts}); retrying next cycle"
                    )
                    if attempts >= 3:
                        details = {
                            "capability": self._last_blocked_cap,
                            "attempts": attempts,
                        }
                        return SolveResult(
                            ok=False,
                            final=snap,
                            blocker=WhyNot("llm_blocked", details=details),
                            steps=step + 1,
                        )
                    continue

                print("❌ No progress made (digest seen before)")
                return SolveResult(ok=False, final=snap, blocker=WhyNot("no_progress"), steps=step + 1)
            seen.add(new_digest)
            
        print(f"❌ Max steps ({max_steps}) reached")
        return SolveResult(ok=False, final=snap, blocker=WhyNot("budget", details={"steps": max_steps}), steps=max_steps)

    def _find_ready(self, snap: Snapshot) -> List[Capability]:
        ready: List[Capability] = []
        for cap in self.capabilities:
            # Rule: all reads exist
            if any(not snap.exists(k) for k in (cap.reads or set())):
                continue
            # Rule: some write missing OR reads changed
            writes_missing = any(not snap.exists(k) for k in (cap.writes or set()))
            read_digest = _digest_reads(cap, snap)
            reads_changed = self._last_read_digest.get(cap.id) != read_digest
            if writes_missing or reads_changed:
                ready.append(cap)
        return ready

    def _pick_compatible(self, ready: List[Capability]) -> List[Capability]:
        # Evidence-first bias: prefer Steps (compute) before Tools (actions)
        # Then sort by number of writes desc, then greedily add if disjoint writes
        def sort_key(cap: Capability) -> tuple:
            is_compute = "compute" in (cap.tags or set())
            num_writes = len(cap.writes or set())
            return (not is_compute, -num_writes)  # compute first, then more writes first
        
        sorted_ready = sorted(ready, key=sort_key)
        plan: List[Capability] = []
        used_writes: Set[str] = set()
        for cap in sorted_ready:
            if used_writes & set(cap.writes or set()):
                continue
            plan.append(cap)
            used_writes |= set(cap.writes or set())
        return plan

    def _apply_batch(
        self,
        ws: Workspace,
        plan: List[Capability],
        snap: Snapshot,
        goal: Callable[[Snapshot], bool],
    ) -> Snapshot:
        cur = snap
        soft_block_reasons = {"llm_invalid_output", "llm_trivial_tests", "llm_unavailable"}

        # Allow a capability to block a batch without counting as progress so we can retry later.
        blocked_cap: Capability | None = None

        for cap in plan:
            rd = _digest_reads(cap, cur)
            print(f"   🔧 Executing {cap.id}...")

            tags = cap.tags or set()
            context_atoms = None
            if tags.intersection({"action", "llm"}):
                try:
                    context_atoms = self._build_context(cap, cur, goal)
                except Exception as exc:
                    print(f"   ⚠️ Context build failed for {cap.id}: {exc}")

            try:
                writes = cap.runner.run(cap, cur, context=context_atoms) or {}
            except GoalBlocked as blocked:
                if blocked.reason in soft_block_reasons:
                    print(f"   ⚠️ {cap.id} blocked by LLM output quality ({blocked.reason}); retrying later")
                    self._last_read_digest[cap.id] = rd
                    blocked_cap = cap
                    continue
                raise

            # Show tool execution output
            if writes:
                print(f"   📝 {cap.id} wrote: {list(writes.keys())}")

            # Treat empty writes as no-op (enables non-blocking human capabilities)
            if not writes:
                if cap is blocked_cap:
                    # Nothing to commit; exit batch early so the capability can retry.
                    return cur
                self._last_read_digest[cap.id] = rd
                continue

            # Basic post hook (verify) on snapshot-after-writes
            if cap.post is not None:
                temp = ws.snapshot()  # not committing yet
                try:
                    if not cap.post(cur, writes):
                        raise RuntimeError(f"verify_failed: {cap.id}")
                except GoalBlocked as blocked:
                    if blocked.reason in soft_block_reasons:
                        print(f"   ⚠️ {cap.id} post-check blocked ({blocked.reason}); retry scheduled")
                        self._last_read_digest[cap.id] = rd
                        blocked_cap = cap
                        continue
                    raise

            prov_id = f"{cap.id}:{rd}:{digest_writes(writes)}"
            cur = ws.cas_commit(writes, write_specs=cap.write_specs or {}, provenance_id=prov_id)
            self._last_read_digest[cap.id] = rd
        if blocked_cap is not None:
            self._last_batch_blocked = True
            self._last_blocked_cap = blocked_cap.id
            self._blocked_attempts[blocked_cap.id] = self._blocked_attempts.get(blocked_cap.id, 0) + 1
            return cur

        self._last_batch_blocked = False
        if blocked_cap is None and self._last_blocked_cap:
            self._blocked_attempts[self._last_blocked_cap] = 0
            self._last_blocked_cap = None

        return cur

    def _build_context(self, unit: Any, snapshot: Snapshot, goal: Callable[[Snapshot], bool]) -> List[Any]:
        """Build context window using topology system.
        
        Args:
            unit: Unit requiring execution
            snapshot: Current snapshot
            goal: Goal function
            
        Returns:
            Context window atoms
        """
        # Plan execution path
        goal_dict = {
            "name": getattr(goal, "__name__", "unknown"),
            "domain": getattr(goal, "__ranger_goal_domain__", "unknown")
        }
        
        path = plan_path(unit=unit, goal=goal_dict, budget=self.budget)
        
        # Read from memory regions
        memory_atoms = []
        for region in path.memory_like:
            query = {
                "unit": getattr(unit, "id", "unknown"),
                "goal": goal_dict["name"],
                "facets": {"thread_id": getattr(snapshot, "thread_id", "unknown")}
            }
            memory_atoms.extend(region.read(query))
        
        # Summarize per region
        summaries = summarize_regions(path.memory_like, memory_atoms, goal_dict)

        # Run guards
        findings = []
        for guard in path.guards:
            try:
                result = guard.validate(memory_atoms or summaries)
            except Exception as exc:
                raise RuntimeError(f"Guard {guard.key} validation failed: {exc}") from exc
            findings.extend(result.get("findings", []))
            if not result["ok"]:
                memory_atoms = repair_context(memory_atoms or summaries, result, guard)

        # Reconcile overlaps
        reconciled = reconcile_overlaps(summaries, path, goal_dict)

        # Pack into context window
        atoms_for_packing = list(memory_atoms) + reconciled
        context_window = pack_context(atoms_for_packing, self.budget, goal_dict)

        return context_window.atoms + findings

    def _missing_for(self, goal: Callable[[Snapshot], bool], snap: Snapshot) -> Set[str]:
        # Minimal unsatisfied set: keys in goal scope that don't exist
        scope = getattr(goal, "__ranger_goal_scope__", set())
        if not scope:
            return set()
        return {k for k in scope if not snap.exists(k)}
