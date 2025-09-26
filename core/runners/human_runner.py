"""Human collaboration runner implementation."""

from __future__ import annotations
from typing import Dict, Any, List, Optional
from ..workspace import Snapshot
from ..capability import Capability


class HumanRunner:
    """Runner for human collaboration capabilities."""
    
    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
    ):
        """Initialize human runner.
        
        Args:
            title: Title for the human review card
            description: Description of what human should do
            fields: Form fields for human input
        """
        self.title = title
        self.description = description
        self.fields = fields or []
    
    def run(self, cap: Capability, snap: Snapshot) -> Dict[str, Any]:
        """Execute human capability.
        
        For now, this is a no-op that allows execution to continue.
        In the future, this will integrate with ObservationBus to
        post review cards and wait for human submissions.
        
        Args:
            cap: Capability being executed
            snap: Current workspace snapshot
            
        Returns:
            Empty dict (no-op for now)
        """
        # TODO: Integrate with ObservationBus when implemented
        print(f"Human review requested: {self.title}")
        if self.description:
            print(f"Description: {self.description}")
        
        # For now, return empty dict to allow execution to continue
        # This enables non-blocking human capabilities
        return {}
