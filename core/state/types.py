"""Core types for state management with strict typing."""

from __future__ import annotations
from typing import Dict, List, Union, TypedDict, Optional, Literal, Any
from pydantic import BaseModel, ConfigDict

# JSON type hierarchy - strictly typed
JSONScalar = Union[str, int, float, bool, None]
JSONValue = Union[JSONScalar, List[Any], Dict[str, Any]]


class State(BaseModel):
    """Immutable state container with strict validation."""
    
    model_config = ConfigDict(extra="forbid", frozen=False)
    
    data: Dict[str, JSONValue]
    meta: Dict[str, JSONValue]


class Delta(TypedDict):
    """Deterministic state updates - top-level upserts only in V1."""
    
    set: Dict[str, JSONValue]


class Event(BaseModel):
    """Execution event with strict typing."""
    
    model_config = ConfigDict(extra="forbid")
    
    ts: float
    tick: int
    ctx: Optional[str]
    action: Optional[str]
    status: Literal["ok", "fail", "timeout", "noop"]
    ms: int
    notes: str
