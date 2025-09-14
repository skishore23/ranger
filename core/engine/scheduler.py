"""Main scheduler for topology agent execution."""

import time
from dataclasses import dataclass
from typing import List, Tuple, Callable, Any, Dict, Set, Optional
from core.state.types import State, Event
from core.context.model import Context
from core.engine.guards import Dedupe, apply_delta
from core.observe.log import emit


# Global backoff and quota tracking
GLOBAL_BACKOFF: Dict[str, float] = {}
CONTEXT_QUOTAS: Dict[str, Dict[str, int]] = {}  # context_id -> {action_name: count}
DEFAULT_COOLDOWN_SECONDS = 120.0


@dataclass
class RunStats:
    """Statistics from a scheduler run."""
    ticks: int
    steps_ok: int
    steps_fail: int
    steps_noop: int
    last_change_tick: int
    priority_selections: int
    backoff_skips: int
    quota_limits: int


def calculate_weights(state: State, contexts: List[Context]) -> Dict[str, float]:
    """
    Calculate partition-of-unity weights for contexts based on state.
    
    This is a generic implementation that gives equal weight to all contexts.
    Agents should provide their own weight calculation functions for domain-specific logic.
    """
    # Generic weight calculation - all contexts get equal weight by default
    context_ids = [ctx.id for ctx in contexts]
    base_weight = 1.0 / len(context_ids) if context_ids else 1.0
    
    weights = {ctx_id: base_weight for ctx_id in context_ids}
    
    # Normalize to partition of unity (should already be normalized)
    total_weight = sum(weights.values()) or 1.0
    return {k: v / total_weight for k, v in weights.items()}


def calculate_topological_order(ctx: Context, action: Any, state: State, contexts: List[Context]) -> float:
    """
    Calculate topological ordering using adaptive weights.
    
    Replaces hardcoded priorities with state-dependent weights.
    """
    weights = calculate_weights(state, contexts)
    base_weight = weights.get(ctx.id, 0.1)
    
    # Apply cooldown to prevent tight loops (topological guard)
    backoff_key = f"{ctx.id}:{action.name}"
    last_run = GLOBAL_BACKOFF.get(backoff_key, 0.0)
    time_since_last = time.time() - last_run
    cooldown_factor = min(time_since_last / DEFAULT_COOLDOWN_SECONDS, 1.0)
    
    return base_weight * cooldown_factor


def should_skip_backoff(ctx: Context, action: Any, state: Optional[State] = None) -> bool:
    """Check if action should be skipped due to backoff."""
    # DISABLED: Cooldown was causing more problems than solving
    # Let the topology drive execution naturally without artificial delays
    return False


def should_skip_quota(ctx: Context, action: Any) -> bool:
    """Check if action should be skipped due to quota limits."""
    # Generic quota system - agents can override this for domain-specific quotas
    # By default, no quota limits are applied
    return False


def update_quota(ctx: Context, action: Any) -> None:
    """Update quota count for executed action."""
    if ctx.id not in CONTEXT_QUOTAS:
        CONTEXT_QUOTAS[ctx.id] = {}
    
    CONTEXT_QUOTAS[ctx.id][action.name] = CONTEXT_QUOTAS[ctx.id].get(action.name, 0) + 1


def detect_quiescence(state: State, tick: int, last_change: int, base_threshold: int = 50) -> bool:
    """Generic quiescence detection with configurable threshold."""
    # Simple quiescence detection - agents can override for domain-specific logic
    return tick - last_change > base_threshold


def run(
    state: State,
    contexts: List[Context],
    is_goal: Callable[[State], bool],
    render: Callable[[], None],
    *,
    max_ticks: int = 1000,
    quiescence_ticks: int = 50,
    logger: Callable[[Event], None] = emit
) -> RunStats:
    """
    Main execution loop for topology agent with context-driven selection.
    
    Execution emerges from context validity predicates and topological ordering.
    More specific contexts (deeper in dependency graph) execute first.
    
    Runs until goal is reached, quiescence detected, or max_ticks exceeded.
    """
    dedupe = Dedupe()
    tick = 0
    last_change = 0
    ok = fail = noop = 0
    priority_selections = backoff_skips = quota_limits = 0
    
    # Track visits for visualization
    edge_visits: Dict[Tuple[str, str], int] = {}
    node_visits: Dict[str, int] = {}
    guard_nodes: Set[str] = set()
    execution_path: List[str] = []
    
    print(f"\n🚀 SCHEDULER: Starting topological agent execution")
    print(f"   🎯 Goal: {is_goal.__name__ if hasattr(is_goal, '__name__') else 'Custom goal'}")
    print(f"   📊 Max ticks: {max_ticks}")
    print(f"   🔄 Contexts: {[ctx.id for ctx in contexts]}")
    
    while tick < max_ticks and not is_goal(state):
        # Find ready actions: valid context + context-owned actions with preconditions met
        ready_candidates: List[Tuple[Context, Any]] = [
            (ctx, action)
            for ctx in contexts
            if ctx.is_valid(state)
            for action in ctx.actions  # Use context-owned actions, not global registry
            if action.allow and action.pre(state)
        ]
        
        # Apply backoff and quota filters
        ready: List[Tuple[Context, Any, float]] = []
        for ctx, action in ready_candidates:
            if should_skip_backoff(ctx, action, state):
                backoff_skips += 1
                continue
            if should_skip_quota(ctx, action):
                quota_limits += 1
                continue
            
            topological_order = calculate_topological_order(ctx, action, state, contexts)
            ready.append((ctx, action, topological_order))
        
        # Sort by topological order (most specific contexts first), then by action name for determinism
        ready.sort(key=lambda x: (x[2], x[1].name), reverse=True)
        
        # Show current topological state
        active_contexts = [ctx.id for ctx in contexts if ctx.is_valid(state)]
        
        if tick % 5 == 0 or len(ready) > 0:  # Log every 5 ticks or when actions are ready
            print(f"\n🌐 TICK {tick}: Topological Regions Active: {active_contexts}")
            if ready:
                ctx, action, order = ready[0]  # Show the selected action
                print(f"   🎯 SELECTED: {ctx.id.upper()} → {action.name}")
                if len(ready) > 1:
                    other_ready = [f"{c.id}→{a.name}" for c, a, _ in ready[1:3]]
                    print(f"   🔄 Also ready: {other_ready}")
                if backoff_skips > 0 or quota_limits > 0:
                    print(f"   ⏸️  Skipped: {backoff_skips} backoff, {quota_limits} quota")
            else:
                quiescence_threshold = detect_quiescence(state, tick, last_change, quiescence_ticks)
                print(f"   💤 No regions ready (quiescence: {quiescence_threshold})")
        
        if not ready:
            # Check for enhanced quiescence detection
            if detect_quiescence(state, tick, last_change, quiescence_ticks):
                print(f"   🛑 QUIESCENCE: Enhanced detection triggered, stopping")
                break
            time.sleep(0.1)
            tick += 1
            continue
        
        any_change = False
        
        # Execute most specific context action (topological ordering)
        ctx, action, topological_order = ready[0]
        priority_selections += 1
        
        # Create signature for deduplication
        sig = (f"{ctx.id}:{action.name}", hash(str(state.data)))
        
        if dedupe.seen(sig):
            print(f"   🔄 DEDUPE: Skipping {ctx.id.upper()}→{action.name} (already executed)")
            guard_nodes.add(ctx.id)  # Mark as guard hit
            tick += 1
            continue
        t0 = time.time()
        
        try:
            # Pass context info to action for micro logging
            action_args = action.args(state)
            action_args["_tick"] = tick
            action_args["_context_id"] = ctx.id
            
            # Execute action
            delta = action.run(state, **action_args)
            
            if delta is None or not apply_delta(state, delta):
                # No-op: action ran but produced no changes
                duration_ms = int((time.time() - t0) * 1000)
                print(f"      ⚪ No changes")
                noop += 1
                logger(Event(
                    ts=time.time(),
                    tick=tick,
                    ctx=ctx.id,
                    action=action.name,
                    status="noop",
                    ms=duration_ms,
                    notes=""
                ))
            else:
                # Success: changes applied
                dedupe.add(sig)
                any_change = True
                last_change = tick
                duration_ms = int((time.time() - t0) * 1000)
                
                # Update backoff and quota tracking
                GLOBAL_BACKOFF[f"{ctx.id}:{action.name}"] = time.time()
                update_quota(ctx, action)
                
                # Track visits for visualization
                node_visits[ctx.id] = node_visits.get(ctx.id, 0) + 1
                if execution_path and execution_path[-1] != ctx.id:
                    # Track edge from last context to current
                    last_ctx = execution_path[-1]
                    edge_key = (last_ctx, ctx.id) if last_ctx < ctx.id else (ctx.id, last_ctx)
                    edge_visits[edge_key] = edge_visits.get(edge_key, 0) + 1
                
                if not execution_path or execution_path[-1] != ctx.id:
                    execution_path.append(ctx.id)
                
                # Show what changed (clean format)
                changes = list(delta.get("set", {}).keys()) if isinstance(delta, dict) else []
                if changes:
                    print(f"      ✅ Updated: {', '.join(changes[:3])}{'...' if len(changes) > 3 else ''}")
                else:
                    print(f"      ✅ Completed")
                
                ok += 1
                logger(Event(
                    ts=time.time(),
                    tick=tick,
                    ctx=ctx.id,
                    action=action.name,
                    status="ok",
                    ms=duration_ms,
                    notes=""
                ))
                
        except Exception as e:
            # Failure: action threw exception
            duration_ms = int((time.time() - t0) * 1000)
            print(f"      ❌ ERROR: {str(e)}")
            fail += 1
            logger(Event(
                ts=time.time(),
                tick=tick,
                ctx=ctx.id,
                action=action.name,
                status="fail",
                ms=duration_ms,
                notes=str(e)
            ))
        
        if any_change:
            # Store visualization data in state for render function
            state.meta["execution_path"] = execution_path
            state.meta["edge_visits"] = edge_visits
            state.meta["node_visits"] = node_visits
            state.meta["guard_nodes"] = guard_nodes
            try:
                render()  # Refresh visualization when state changes
            except Exception as e:
                print(f"Warning: Failed to render PNG: {e}")
        
        tick += 1
    
    # Final status
    if is_goal(state):
        print(f"\n🎉 SCHEDULER: Goal reached after {tick} ticks!")
    elif tick >= max_ticks:
        print(f"\n⏰ SCHEDULER: Max ticks ({max_ticks}) reached")
    else:
        print(f"\n🛑 SCHEDULER: Stopped due to quiescence after {tick} ticks")
    
    return RunStats(
        ticks=tick,
        steps_ok=ok,
        steps_fail=fail,
        steps_noop=noop,
        last_change_tick=last_change,
        priority_selections=priority_selections,
        backoff_skips=backoff_skips,
        quota_limits=quota_limits
    )
