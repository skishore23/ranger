"""Comprehensive audit logging for debugging agent behavior."""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, asdict


@dataclass
class AuditEvent:
    """Structured audit event for JSONL logging."""
    timestamp: float
    tick: int
    event_type: str  # "llm_call", "action_execution", "context_transition", "custom"
    context: str
    action: str
    data: Dict[str, Any]
    success: bool
    duration_ms: Optional[float] = None
    error: Optional[str] = None


class AuditLogger:
    """Centralized audit logger for agent debugging."""
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.current_tick = 0
        
    def set_tick(self, tick: int):
        """Update current tick for all subsequent logs."""
        self.current_tick = tick
        
    def log_llm_call(self, context: str, action: str, prompt: str, response: str, 
                     duration_ms: float, success: bool = True, error: str = None):
        """Log LLM interaction with full prompt and response."""
        event = AuditEvent(
            timestamp=time.time(),
            tick=self.current_tick,
            event_type="llm_call",
            context=context,
            action=action,
            data={
                "prompt": prompt,
                "response": response,
                "prompt_length": len(prompt),
                "response_length": len(response)
            },
            success=success,
            duration_ms=duration_ms,
            error=error
        )
        self._write_event(event)
        
    def log_action_execution(self, context: str, action: str, execution_data: Dict,
                            success: bool = True, error: str = None, duration_ms: float = None):
        """Log action execution with generic data."""
        event = AuditEvent(
            timestamp=time.time(),
            tick=self.current_tick,
            event_type="action_execution",
            context=context,
            action=action,
            data=execution_data,
            success=success,
            duration_ms=duration_ms,
            error=error
        )
        self._write_event(event)
        
    def log_custom_event(self, context: str, action: str, event_data: Dict,
                        success: bool = True, error: str = None):
        """Log custom domain-specific events."""
        event = AuditEvent(
            timestamp=time.time(),
            tick=self.current_tick,
            event_type="custom",
            context=context,
            action=action,
            data=event_data,
            success=success,
            error=error
        )
        self._write_event(event)
        
    def log_context_transition(self, from_context: str, to_context: str, 
                              reason: str, state_data: Dict):
        """Log context transitions and state changes."""
        event = AuditEvent(
            timestamp=time.time(),
            tick=self.current_tick,
            event_type="context_transition",
            context=f"{from_context} -> {to_context}",
            action="transition",
            data={
                "reason": reason,
                "state_keys": list(state_data.keys()),
                "state_size": len(state_data)
            },
            success=True
        )
        self._write_event(event)
        
    def _write_event(self, event: AuditEvent):
        """Write event to JSONL file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(asdict(event)) + '\n')
        except Exception as e:
            print(f"Failed to write audit log: {e}")


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def init_audit_logger(log_file: Path):
    """Initialize global audit logger."""
    global _audit_logger
    _audit_logger = AuditLogger(log_file)
    
    
def get_audit_logger() -> Optional[AuditLogger]:
    """Get global audit logger instance."""
    return _audit_logger
