"""Demo actions for topology agent - using context-owned actions pattern."""

from core.action.builtin_http import HttpGet
from core.action.builtin_llm import LlmSummarize
from core.action.builtin_io import WriteResult


def get_demo_actions():
    """Get demo actions for context ownership - no global registry."""
    return [
        HttpGet(),
        LlmSummarize(), 
        WriteResult()
    ]
