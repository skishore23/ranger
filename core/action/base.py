"""Base action protocol with strict typing."""

from __future__ import annotations
from typing import Protocol, Dict, List, Optional
from core.state.types import State, Delta, JSONValue


class Action(Protocol):
    """Protocol defining the action interface."""
    
    name: str
    locks: List[str]
    timeout_s: int
    max_retries: int
    allow: bool
    
    def pre(self, state: State) -> bool:
        """Check if action preconditions are met."""
        ...
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract arguments from state for action execution."""
        ...
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Execute action and return state delta, or None if no changes."""
        ...
