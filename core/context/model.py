"""Context model for topology regions."""

from typing import Callable, Final, List, Optional, Any
from core.state.types import State

# Type alias for context validation function
IsValid = Callable[[State], bool]


class Context:
    """Represents a topology region with validation, resources, and owned actions.
    
    In pure topological systems, contexts own their actions - execution emerges
    from context validity, not global action availability.
    """
    
    def __init__(
        self, 
        id: str, 
        label: str, 
        is_valid: IsValid, 
        resources: Optional[List[str]] = None,
        actions: Optional[List[Any]] = None
    ) -> None:
        self.id: Final[str] = id
        self.label: Final[str] = label
        self.is_valid = is_valid
        self.resources = resources or []
        self.actions = actions or []  # Context-owned actions
