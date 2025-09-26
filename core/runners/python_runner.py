"""Python function runner implementation."""

from __future__ import annotations
from typing import Callable, Dict, Any, Optional
from ..workspace import Snapshot
from ..capability import Capability


class PythonRunner:
    """Runner for Python function capabilities."""
    
    def __init__(self, fn: Callable[[Snapshot], Optional[Dict[str, Any]]]):
        """Initialize with Python function.
        
        Args:
            fn: Function that takes Snapshot and returns dict or None
        """
        self._fn = fn

    def run(self, cap: Capability, snap: Snapshot) -> Dict[str, Any]:
        """Execute Python function capability.
        
        Args:
            cap: Capability being executed
            snap: Current workspace snapshot
            
        Returns:
            Dictionary of field updates
            
        Raises:
            TypeError: If function returns non-dict/None
            RuntimeError: If function writes undeclared fields
        """
        result = self._fn(snap)
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise TypeError("Capability function must return dict or None")
        
        # Enforce write locality - capability can only update declared fields
        illegal = set(result.keys()) - set(cap.writes or set())
        if illegal:
            raise RuntimeError(f"Capability {cap.id} wrote undeclared fields: {sorted(illegal)}")
        
        return result
