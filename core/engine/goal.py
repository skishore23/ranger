"""Goal checking for topology agent."""

from core.state.types import State


def goal(state: State) -> bool:
    """Check if the goal state has been reached."""
    return bool(state.data.get("published"))
