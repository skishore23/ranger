from __future__ import annotations
from typing import List, Dict, Any, Callable, Tuple, Set
from .workspace import Workspace, Snapshot
from .capability import Capability
from .errors import SolveResult, WhyNot
import hashlib
import json
from .provenance import digest_writes


def _digest_reads(cap: Capability, snap: Snapshot) -> str:
    keys = sorted(list(cap.reads or set()))
    vals = [(k, snap.get(k, None)) for k in keys]
    payload = json.dumps(vals, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Engine:
    def __init__(self, capabilities: List[Capability]):
        self.capabilities = capabilities
        self._last_read_digest: Dict[str, str] = {}

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
            goal_met = False if goal_blocked_by_scope else goal(snap)
            
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
            snap = self._apply_batch(ws, plan, snap)
            new_digest = snap.digest()
            
            print(f"   State after: {list(snap.data.keys())}")
            print(f"   Digest: {old_digest[:8]} → {new_digest[:8]}")
            
            if new_digest in seen:
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

    def _apply_batch(self, ws: Workspace, plan: List[Capability], snap: Snapshot) -> Snapshot:
        cur = snap
        for cap in plan:
            rd = _digest_reads(cap, cur)
            writes = cap.runner.run(cap, cur) or {}
            
            # Treat empty writes as no-op (enables non-blocking human capabilities)
            if not writes:
                self._last_read_digest[cap.id] = rd
                continue
            
            # Basic post hook (verify) on snapshot-after-writes
            if cap.post is not None:
                temp = ws.snapshot()  # not committing yet
                if not cap.post(cur, writes):
                    raise RuntimeError(f"verify_failed: {cap.id}")
            
            prov_id = f"{cap.id}:{rd}:{digest_writes(writes)}"
            cur = ws.cas_commit(writes, write_specs=cap.write_specs or {}, provenance_id=prov_id)
            self._last_read_digest[cap.id] = rd
        return cur

    def _missing_for(self, goal: Callable[[Snapshot], bool], snap: Snapshot) -> Set[str]:
        # Minimal unsatisfied set: keys in goal scope that don't exist
        scope = getattr(goal, "__ranger_goal_scope__", set())
        if not scope:
            return set()
        return {k for k in scope if not snap.exists(k)}
