"""Tests for enhanced scheduler with priority-based selection and ReAct micros."""

import time
from unittest.mock import Mock, patch
import pytest
from core.state.types import State
from core.context.model import Context
from core.engine.scheduler import (
    calculate_priority, should_skip_backoff, should_skip_quota, 
    detect_quiescence, GLOBAL_BACKOFF, CONTEXT_QUOTAS
)
from core.action.react_micro import BoundedReActMicro, ReActPlan, ReActResult, ReActObservation
from tests.helpers.kit import mk_state, mk_context


class MockAction:
    """Mock action for testing."""
    
    def __init__(self, name: str):
        self.name = name
        self.allow = True
    
    def pre(self, state: State) -> bool:
        return True
    
    def args(self, state: State) -> dict:
        return {}


def test_priority_calculation_coverage_actions():
    """Test that coverage actions get higher priority."""
    state = mk_state()
    ctx = mk_context("test_ctx")
    
    coverage_action = MockAction("run_pytest_cov")
    regular_action = MockAction("regular_action")
    
    coverage_priority = calculate_priority(ctx, coverage_action, state)
    regular_priority = calculate_priority(ctx, regular_action, state)
    
    assert coverage_priority > regular_priority
    assert coverage_priority >= 3.0  # Base 1.0 + coverage 2.0


def test_priority_calculation_repair_actions_with_failures():
    """Test that repair actions get higher priority when there are failures."""
    state = mk_state()
    state.data["test_results"] = {"failed": 3, "passed": 2}
    
    ctx = mk_context("needs_refinement")
    repair_action = MockAction("repair_micro")
    regular_action = MockAction("regular_action")
    
    repair_priority = calculate_priority(ctx, repair_action, state)
    regular_priority = calculate_priority(ctx, regular_action, state)
    
    assert repair_priority > regular_priority
    # Base 1.0 + repair 1.5 (min(3*0.5, 3.0)) + refinement context 2.0 = 4.5
    assert repair_priority >= 4.0


def test_backoff_system():
    """Test that backoff system prevents rapid re-execution."""
    # Clear global state
    GLOBAL_BACKOFF.clear()
    
    ctx = mk_context("test_ctx")
    action = MockAction("test_action")
    
    # First check should pass
    assert not should_skip_backoff(ctx, action)
    
    # Simulate recent execution
    GLOBAL_BACKOFF[f"{ctx.id}:{action.name}"] = time.time()
    
    # Second check should fail (within cooldown)
    assert should_skip_backoff(ctx, action)
    
    # Simulate time passing
    GLOBAL_BACKOFF[f"{ctx.id}:{action.name}"] = time.time() - 200  # 200 seconds ago
    
    # Should pass again after cooldown
    assert not should_skip_backoff(ctx, action)


def test_quota_system():
    """Test that quota system limits action executions."""
    # Clear global state
    CONTEXT_QUOTAS.clear()
    
    ctx = mk_context("tests_generated")
    action = MockAction("gen_tests_with_repair")
    
    # First few checks should pass
    for i in range(5):
        assert not should_skip_quota(ctx, action)
        # Simulate quota increment (normally done by scheduler)
        if ctx.id not in CONTEXT_QUOTAS:
            CONTEXT_QUOTAS[ctx.id] = {}
        CONTEXT_QUOTAS[ctx.id][action.name] = CONTEXT_QUOTAS[ctx.id].get(action.name, 0) + 1
    
    # Should hit quota limit
    assert should_skip_quota(ctx, action)


def test_enhanced_quiescence_detection():
    """Test enhanced quiescence detection with state-dependent thresholds."""
    state = mk_state()
    
    # Base case - normal threshold
    assert not detect_quiescence(state, tick=40, last_change=0, base_threshold=50)
    assert detect_quiescence(state, tick=60, last_change=0, base_threshold=50)
    
    # With failing tests - reduced threshold
    state.data["test_results"] = {"failed": 2}
    assert detect_quiescence(state, tick=25, last_change=0, base_threshold=50)  # 50//3 = 16, so 25 > 16
    
    # With coverage improvement - increased threshold
    state.data = {"coverage_improvement": 15.0}
    assert not detect_quiescence(state, tick=80, last_change=0, base_threshold=50)  # 50*2 = 100
    assert detect_quiescence(state, tick=120, last_change=0, base_threshold=50)


class TestReActMicro(BoundedReActMicro):
    """Test implementation of bounded ReAct micro."""
    
    name = "test_react_micro"
    locks = ["test"]
    timeout_s = 30
    max_attempts = 2
    allow = True
    
    def __init__(self):
        super().__init__()
        self.reason_calls = []
        self.act_calls = []
        self.observe_calls = []
        self.revise_calls = []
    
    def pre(self, state: State) -> bool:
        return True
    
    def args(self, state: State) -> dict:
        return {"test_data": "value"}
    
    def _reason_step(self, state: State, attempt: int, **kwargs) -> ReActPlan:
        self.reason_calls.append(attempt)
        if attempt == 0:
            return ReActPlan(
                action_type="test_action",
                parameters={"param": "value"},
                confidence=0.8,
                reasoning="Test reasoning"
            )
        return None  # Stop after first attempt for testing
    
    def _act_step(self, plan: ReActPlan, state: State, **kwargs) -> ReActResult:
        self.act_calls.append(plan.action_type)
        return ReActResult(
            success=True,
            data={"result": "success"},
            metadata={"tokens": 100}
        )
    
    def _observe_step(self, result: ReActResult, state: State, **kwargs) -> ReActObservation:
        self.observe_calls.append(result.success)
        return ReActObservation(
            is_valid=True,
            feedback="Test successful",
            should_retry=False,
            confidence=0.9
        )
    
    def _create_success_delta(self, result: ReActResult, state: State, **kwargs):
        return {"set": {"test_result": result.data["result"]}}
    
    def _revise_step(self, observation: ReActObservation, attempt: int, **kwargs) -> None:
        self.revise_calls.append(attempt)


def test_bounded_react_micro_execution():
    """Test that bounded ReAct micro executes correctly."""
    micro = TestReActMicro()
    state = mk_state()
    
    # Execute the micro
    delta = micro.run(state, _tick=1, _context_id="test_ctx")
    
    # Verify execution flow
    assert len(micro.reason_calls) == 1
    assert len(micro.act_calls) == 1
    assert len(micro.observe_calls) == 1
    assert len(micro.revise_calls) == 0  # No revision needed for successful case
    
    # Verify result
    assert delta is not None
    assert delta["set"]["test_result"] == "success"


def test_bounded_react_micro_attempt_limit():
    """Test that ReAct micro respects attempt limits."""
    
    class FailingMicro(TestReActMicro):
        def _observe_step(self, result: ReActResult, state: State, **kwargs) -> ReActObservation:
            self.observe_calls.append(result.success)
            return ReActObservation(
                is_valid=False,
                feedback="Test failed",
                should_retry=True,
                confidence=0.3
            )
        
        def _reason_step(self, state: State, attempt: int, **kwargs) -> ReActPlan:
            self.reason_calls.append(attempt)
            # Always return a plan to test attempt limiting
            return ReActPlan(
                action_type="test_action",
                parameters={"param": "value"},
                confidence=0.8 - attempt * 0.2,
                reasoning=f"Test reasoning attempt {attempt}"
            )
    
    micro = FailingMicro()
    state = mk_state()
    
    # Execute the micro
    delta = micro.run(state, _tick=1, _context_id="test_ctx")
    
    # Should have tried max_attempts times
    assert len(micro.reason_calls) == micro.max_attempts
    assert len(micro.act_calls) == micro.max_attempts
    assert len(micro.observe_calls) == micro.max_attempts
    assert len(micro.revise_calls) == micro.max_attempts - 1  # No revision after last attempt
    
    # Should return None after all attempts failed
    assert delta is None


def test_bounded_react_micro_timeout():
    """Test that ReAct micro respects timeout."""
    
    class SlowMicro(TestReActMicro):
        timeout_s = 1  # Very short timeout
        
        def _act_step(self, plan: ReActPlan, state: State, **kwargs) -> ReActResult:
            time.sleep(2)  # Sleep longer than timeout
            return super()._act_step(plan, state, **kwargs)
    
    micro = SlowMicro()
    state = mk_state()
    
    # Should raise timeout exception or return None
    with pytest.raises(Exception):  # Timeout should cause exception
        micro.run(state, _tick=1, _context_id="test_ctx")


@pytest.mark.parametrize("priority_weight,expected_min", [
    (2.0, 3.0),  # coverage actions
    (3.0, 4.0),  # repair actions with failures
    (1.0, 2.0),  # cli actions
])
def test_priority_weights(priority_weight, expected_min):
    """Test different priority weight configurations."""
    state = mk_state()
    if priority_weight == 3.0:  # repair case
        state.data["test_results"] = {"failed": 2}
    
    ctx = mk_context("test_ctx")
    action = MockAction("test_action")
    
    # Mock the action name to trigger specific priority logic
    if priority_weight == 2.0:
        action.name = "run_pytest_cov"
    elif priority_weight == 3.0:
        action.name = "repair_micro"
        ctx.id = "needs_refinement"
    elif priority_weight == 1.0:
        action.name = "cli_action"
    
    priority = calculate_priority(ctx, action, state)
    assert priority >= expected_min


def test_scheduler_integration_with_priorities():
    """Test that scheduler properly uses priority system."""
    # This would be an integration test with the actual scheduler
    # For now, we test the individual components
    
    state = mk_state()
    state.data["test_results"] = {"failed": 1}
    
    ctx1 = mk_context("regular_ctx")
    ctx2 = mk_context("needs_refinement")
    
    action1 = MockAction("regular_action")
    action2 = MockAction("repair_micro")
    
    priority1 = calculate_priority(ctx1, action1, state)
    priority2 = calculate_priority(ctx2, action2, state)
    
    # Repair action in refinement context should have higher priority
    assert priority2 > priority1
    
    # Verify that ready list would be sorted correctly
    ready = [(ctx1, action1, priority1), (ctx2, action2, priority2)]
    ready.sort(key=lambda x: x[2], reverse=True)
    
    assert ready[0][1].name == "repair_micro"  # Highest priority first
