"""Action system for topology agent.

Functional action patterns with Either types for pure morphisms:
- safe: Functional error handling without exceptions
- Either: Left/Right types for composable error handling  
- Functional actions: Pure morphisms State → Either[Error, Delta]
"""

from .safe import Either, Left, Right, SafeError, safe, safe_async
from .safe import safe_subprocess, safe_file_read, safe_file_write, safe_json_parse
from .safe import sequence_either, traverse_either

__all__ = [
    "Either", "Left", "Right", "SafeError",
    "safe", "safe_async", 
    "safe_subprocess", "safe_file_read", "safe_file_write", "safe_json_parse",
    "sequence_either", "traverse_either"
]
