"""Test kit providing stable helpers for LLM-generated tests."""

from __future__ import annotations
from typing import Callable, List
from core.state.types import State, Delta
from core.context.model import Context
from core.engine.scheduler import run, RunStats
from core.engine.guards import apply_delta
from core.observe.log import emit as default_emit


def mk_state() -> State:
    """Create a fresh test state."""
    return State(data={}, meta={})


def mk_context(id: str = "test", valid: bool = True) -> Context:
    """Create a test context with given validity."""
    return Context(id, id.title(), (lambda s: valid), resources=[])


class NoopAction:
    """Test action that produces no delta."""
    
    name: str = "noop_action"
    locks: List[str] = []
    timeout_s: int = 5
    max_retries: int = 0
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Always ready to run."""
        return True
    
    def args(self, state: State) -> dict:
        """No arguments needed."""
        return {}
    
    def run(self, state: State) -> None:
        """Return None delta (no-op)."""
        return None


def run_engine(
    state: State,
    contexts: List[Context],
    is_goal: Callable[[State], bool],
    *,
    ticks: int = 100,
    q: int = 10
) -> RunStats:
    """Run engine with simple renderer and pass-through logger for deterministic tests."""
    def _render() -> None:
        pass
    
    return run(
        state, 
        contexts, 
        is_goal, 
        _render, 
        max_ticks=ticks, 
        quiescence_ticks=q, 
        logger=default_emit
    )
