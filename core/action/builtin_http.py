"""HTTP action for fetching web content."""

import httpx
from typing import Dict, List, Optional
from core.state.types import State, Delta, JSONValue


class HttpGet:
    """HTTP GET action for fetching web content."""
    
    name: str = "http_get"
    locks: List[str] = ["net"]
    timeout_s: int = 20
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if URL exists and docs not already fetched."""
        return "url" in state.data and "docs" not in state.data
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract URL from state."""
        return {"url": state.data["url"]}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Fetch URL content and return delta with docs."""
        url = str(kwargs["url"])
        
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.get(url)
            response.raise_for_status()
            text = response.text
        
        return {"set": {"docs": text}}
