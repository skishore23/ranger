"""State store operations with functional approach."""

import json
from pathlib import Path
from typing import Optional
from core.state.types import State, Delta, JSONValue


def get_state() -> State:
    """Create initial empty state."""
    return State(data={}, meta={})


def apply_delta(state: State, delta: Delta) -> bool:
    """Apply delta to state, return True if any changes occurred."""
    changed = False
    for key, value in delta["set"].items():
        if state.data.get(key) != value:
            state.data[key] = value
            changed = True
    return changed


def snapshot_state(state: State, path: Path) -> None:
    """Save state snapshot to file."""
    with open(path, "w") as f:
        json.dump(state.model_dump(), f, indent=2)


def load_state(path: Path) -> Optional[State]:
    """Load state from snapshot file."""
    if not path.exists():
        return None
    
    with open(path, "r") as f:
        data = json.load(f)
    
    return State(**data)
