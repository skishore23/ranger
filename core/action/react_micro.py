"""Base classes for bounded ReAct micros."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from core.state.types import State, Delta, JSONValue
from core.action.base import Action
from core.observe.log import emit, Event


@dataclass
class ReActPlan:
    """Plan generated during ReAct reasoning phase."""
    
    action_type: str
    parameters: Dict[str, Any]
    confidence: float
    reasoning: str


@dataclass
class ReActResult:
    """Result from ReAct action phase."""
    
    success: bool
    data: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = None


@dataclass
class ReActObservation:
    """Observation from ReAct observe phase."""
    
    is_valid: bool
    feedback: str
    should_retry: bool
    confidence: float


class BoundedReActMicro(Action, ABC):
    """Base class for bounded ReAct micros with hard attempt limits."""
    
    # Subclasses must define these
    max_attempts: int = 2
    timeout_s: int = 120
    
    def __init__(self):
        """Initialize ReAct micro with attempt tracking."""
        self._current_attempt = 0
        self._start_time = 0.0
        self._tick = 0
        self._context_id = ""
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Execute bounded ReAct micro with comprehensive logging."""
        self._start_time = time.time()
        self._tick = kwargs.get("_tick", 0)
        self._context_id = kwargs.get("_context_id", "unknown")
        
        self._log_micro_start()
        
        try:
            return self._run_micro_loop(state, **kwargs)
        except Exception as e:
            self._log_micro_error(str(e))
            raise
        finally:
            self._log_micro_end()
    
    def _run_micro_loop(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Main ReAct micro loop: reason→act→observe→revise."""
        
        for attempt in range(self.max_attempts):
            self._current_attempt = attempt
            
            # Check timeout
            if time.time() - self._start_time > self.timeout_s:
                self._log_timeout()
                raise TimeoutError(f"ReAct micro '{self.name}' timed out after {self.timeout_s}s")
            
            try:
                # REASON: analyze state and create plan
                plan = self._reason_step(state, attempt, **kwargs)
                if not plan:
                    self._log_no_plan(attempt)
                    break
                
                self._log_reason_success(plan)
                
                # ACT: execute the planned action
                result = self._act_step(plan, state, **kwargs)
                self._log_act_result(result)
                # Enforce timeout immediately after action execution
                if time.time() - self._start_time > self.timeout_s:
                    self._log_timeout()
                    raise TimeoutError(f"ReAct micro '{self.name}' timed out after {self.timeout_s}s during act phase")
                
                # OBSERVE: validate result and get feedback
                observation = self._observe_step(result, state, **kwargs)
                self._log_observation(observation)
                
                if observation.is_valid:
                    # Success - create delta and return
                    delta = self._create_success_delta(result, state, **kwargs)
                    self._log_success(delta)
                    return delta
                
                # REVISE: prepare for next attempt if needed
                if attempt < self.max_attempts - 1 and observation.should_retry:
                    self._revise_step(observation, attempt, **kwargs)
                    self._log_revise(observation, attempt)
                else:
                    self._log_give_up(observation, attempt)
                    break
                    
            except Exception as e:
                self._log_attempt_error(attempt, str(e))
                if attempt == self.max_attempts - 1:
                    raise
                continue
        
        # All attempts failed
        self._log_all_attempts_failed()
        return None
    
    # Abstract methods that subclasses must implement
    
    @abstractmethod
    def _reason_step(self, state: State, attempt: int, **kwargs: JSONValue) -> Optional[ReActPlan]:
        """Reason about what action to take. Return None to stop trying."""
        pass
    
    @abstractmethod
    def _act_step(self, plan: ReActPlan, state: State, **kwargs: JSONValue) -> ReActResult:
        """Execute the planned action."""
        pass
    
    @abstractmethod
    def _observe_step(self, result: ReActResult, state: State, **kwargs: JSONValue) -> ReActObservation:
        """Observe the result and decide if it's valid."""
        pass
    
    @abstractmethod
    def _create_success_delta(self, result: ReActResult, state: State, **kwargs: JSONValue) -> Delta:
        """Create state delta from successful result."""
        pass
    
    def _revise_step(self, observation: ReActObservation, attempt: int, **kwargs: JSONValue) -> None:
        """Revise approach for next attempt. Default: no-op."""
        pass
    
    # Logging methods
    
    def _log_micro_start(self) -> None:
        """Log start of ReAct micro."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:start",
            status="ok",
            ms=0,
            notes=f"max_attempts={self.max_attempts}"
        ))
    
    def _log_micro_end(self) -> None:
        """Log end of ReAct micro."""
        duration_ms = int((time.time() - self._start_time) * 1000)
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:end",
            status="ok",
            ms=duration_ms,
            notes=f"total_attempts={self._current_attempt + 1}"
        ))
    
    def _log_reason_success(self, plan: ReActPlan) -> None:
        """Log successful reasoning step."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:reason",
            status="ok",
            ms=0,
            notes=f"attempt={self._current_attempt} action_type={plan.action_type} confidence={plan.confidence:.2f}"
        ))
    
    def _log_act_result(self, result: ReActResult) -> None:
        """Log action execution result."""
        status = "ok" if result.success else "fail"
        notes = f"attempt={self._current_attempt}"
        if result.error:
            notes += f" error={result.error[:100]}"
        if result.metadata:
            notes += f" metadata={str(result.metadata)[:50]}"
        
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:act",
            status=status,
            ms=0,
            notes=notes
        ))
    
    def _log_observation(self, observation: ReActObservation) -> None:
        """Log observation result."""
        status = "ok" if observation.is_valid else "fail"
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:observe",
            status=status,
            ms=0,
            notes=f"attempt={self._current_attempt} valid={observation.is_valid} retry={observation.should_retry} confidence={observation.confidence:.2f}"
        ))
    
    def _log_revise(self, observation: ReActObservation, attempt: int) -> None:
        """Log revision step."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:revise",
            status="ok",
            ms=0,
            notes=f"attempt={attempt} feedback={observation.feedback[:100]}"
        ))
    
    def _log_success(self, delta: Delta) -> None:
        """Log successful completion."""
        changes = list(delta.get("set", {}).keys()) if isinstance(delta, dict) else []
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:success",
            status="ok",
            ms=0,
            notes=f"attempt={self._current_attempt} changes={changes}"
        ))
    
    def _log_no_plan(self, attempt: int) -> None:
        """Log when reasoning produces no plan."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:no_plan",
            status="noop",
            ms=0,
            notes=f"attempt={attempt}"
        ))
    
    def _log_give_up(self, observation: ReActObservation, attempt: int) -> None:
        """Log when giving up after failed attempt."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:give_up",
            status="fail",
            ms=0,
            notes=f"attempt={attempt} feedback={observation.feedback[:100]}"
        ))
    
    def _log_all_attempts_failed(self) -> None:
        """Log when all attempts have failed."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:all_failed",
            status="fail",
            ms=0,
            notes=f"max_attempts={self.max_attempts}"
        ))
    
    def _log_timeout(self) -> None:
        """Log timeout."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:timeout",
            status="fail",
            ms=self.timeout_s * 1000,
            notes=f"timeout_s={self.timeout_s}"
        ))
    
    def _log_attempt_error(self, attempt: int, error: str) -> None:
        """Log error during attempt."""
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:attempt_error",
            status="fail",
            ms=0,
            notes=f"attempt={attempt} error={error[:100]}"
        ))
    
    def _log_micro_error(self, error: str) -> None:
        """Log overall micro error."""
        duration_ms = int((time.time() - self._start_time) * 1000)
        emit(Event(
            ts=time.time(),
            tick=self._tick,
            ctx=self._context_id,
            action=f"{self.name}:micro_error",
            status="fail",
            ms=duration_ms,
            notes=f"error={error[:100]}"
        ))


class DeterministicAction(Action, ABC):
    """Base class for deterministic actions (no ReAct needed)."""
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Execute deterministic action with simple logging."""
        start_time = time.time()
        tick = kwargs.get("_tick", 0)
        context_id = kwargs.get("_context_id", "unknown")
        
        try:
            delta = self._execute(state, **kwargs)
            
            duration_ms = int((time.time() - start_time) * 1000)
            status = "ok" if delta else "noop"
            changes = list(delta.get("set", {}).keys()) if delta and isinstance(delta, dict) else []
            
            emit(Event(
                ts=time.time(),
                tick=tick,
                ctx=context_id,
                action=self.name,
                status=status,
                ms=duration_ms,
                notes=f"changes={changes}" if changes else ""
            ))
            
            return delta
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            emit(Event(
                ts=time.time(),
                tick=tick,
                ctx=context_id,
                action=self.name,
                status="fail",
                ms=duration_ms,
                notes=f"error={str(e)[:100]}"
            ))
            raise
    
    @abstractmethod
    def _execute(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Execute the deterministic action."""
        pass
