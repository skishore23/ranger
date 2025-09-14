"""IO action for writing results to files."""

from typing import Dict, List, Optional
from pathlib import Path
from core.state.types import State, Delta, JSONValue


class WriteResult:
    """Write result to file action."""
    
    name: str = "write_result"
    locks: List[str] = ["fs"]
    timeout_s: int = 10
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if summary exists and not already published."""
        return "summary" in state.data and not state.data.get("published")
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Return default output path."""
        return {"path": "logs/out.txt"}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Write summary to file and mark as published."""
        path = Path(str(kwargs["path"]))
        summary_content = str(state.data["summary"])
        
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary_content)
        
        return {"set": {"published": True}}
