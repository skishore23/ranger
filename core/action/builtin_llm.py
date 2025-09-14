"""LLM action for content summarization."""

from typing import Dict, List, Optional
from core.state.types import State, Delta, JSONValue
from core.llm.openai_adapter import OpenAIAdapter


class LlmSummarize:
    """LLM summarization action using OpenAI."""
    
    name: str = "llm_summarize"
    locks: List[str] = []
    timeout_s: int = 30
    max_retries: int = 1
    allow: bool = True
    
    def __init__(self) -> None:
        self.llm = OpenAIAdapter()
    
    def pre(self, state: State) -> bool:
        """Check if docs exist and summary not already created."""
        return "docs" in state.data and "summary" not in state.data
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """No additional args needed."""
        return {}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Summarize docs content using LLM."""
        content = str(state.data["docs"])[:6000]  # Truncate for token limits
        
        messages = [
            {
                "role": "user",
                "content": f"Summarize briefly:\n{content}"
            }
        ]
        
        summary = self.llm.chat(messages)
        return {"set": {"summary": summary}}
