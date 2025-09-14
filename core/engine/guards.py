"""Guard mechanisms for preventing loops and managing execution."""

from collections import deque
from typing import Tuple
from core.state.types import State, Delta


class Dedupe:
    """Deduplication mechanism to prevent action loops."""
    
    def __init__(self, maxlen: int = 5000) -> None:
        self._dq: deque[Tuple[str, int]] = deque(maxlen=maxlen)
    
    def seen(self, sig: Tuple[str, int]) -> bool:
        """Check if signature has been seen before."""
        return sig in self._dq
    
    def add(self, sig: Tuple[str, int]) -> None:
        """Add signature to seen set."""
        self._dq.append(sig)


def apply_delta(state: State, delta: Delta) -> bool:
    """
    Apply delta to state and return whether any changes occurred.
    
    This is the core state mutation function with change detection.
    """
    changed = False
    
    for key, value in delta["set"].items():
        if state.data.get(key) != value:
            state.data[key] = value
            changed = True
    
    return changed
