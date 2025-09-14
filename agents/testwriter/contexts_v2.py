"""Test-writer agent contexts V2 - Simplified refinement loop."""

from typing import List
from core.context.model import Context
from core.state.types import State

# Import actions
from agents.testwriter.actions import RunPytestCov, RerunPytestCov, PickTargets, IntrospectApi
from agents.testwriter.generation import SimpleGenerateTestAction
from agents.testwriter.repair_regions import ReasonAboutFailureAction, ActOnFailureAction, ObserveRepairAction, ReviseRepairAction


def repo_ready(state: State) -> bool:
    """Check if repository is ready for test generation."""
    return (
        "repo_path" in state.data and 
        "venv_active" in state.data and 
        "baseline_coverage" not in state.data
    )


def coverage_baselined(state: State) -> bool:
    """Check if baseline coverage measured - gated by failures and pending work."""
    return (
        "baseline_coverage" in state.data and 
        "target_modules" not in state.data and
        not state.data.get("failing_nodeids", []) and  # No failures allowed
        state.data.get("pending_gen", 0) == 0  # No pending generation
    )


def targets_chosen(state: State) -> bool:
    """Check if targets chosen and ready for generation."""
    return (
        "target_modules" in state.data and 
        len(state.data["target_modules"]) > 0 and
        "generated_tests" not in state.data  # Haven't generated tests yet
    )


def tests_generated(state: State) -> bool:
    """Check if tests generated but not yet observed (transition state)."""
    # This is a brief transition state - only active immediately after generation
    # before the scheduler moves to needs_observing
    has_tests = (
        "generated_tests" in state.data and 
        len(state.data.get("generated_tests", {})) > 0
    )
    
    tests_written_ts = state.data.get("tests_written_ts", 0)
    last_test_run_ts = state.data.get("last_test_run_ts", 0)
    
    # Only active if tests are newer AND we haven't started observing yet
    # (no pending_repair flag set)
    needs_first_run = tests_written_ts > last_test_run_ts
    not_observing_yet = state.data.get("pending_repair", 0) == 0
    
    return has_tests and needs_first_run and not_observing_yet


def needs_observing(state: State) -> bool:
    """Check if there's something new to measure OR after repair."""
    has_tests = (
        "generated_tests" in state.data and 
        len(state.data.get("generated_tests", {})) > 0
    )
    
    # Only observe if:
    # 1. Tests newer than last run (first time) OR
    # 2. Pending repair work (after revision)
    tests_written_ts = state.data.get("tests_written_ts", 0)
    last_test_run_ts = state.data.get("last_test_run_ts", 0)
    pending_repair = state.data.get("pending_repair", 0) > 0
    
    needs_first_run = tests_written_ts > last_test_run_ts
    
    return has_tests and (needs_first_run or pending_repair)


def needs_revising(state: State) -> bool:
    """Check if any failing nodeids from the last run need repair."""
    failing_nodeids = state.data.get("failing_nodeids", [])
    has_failures = bool(failing_nodeids) and len(failing_nodeids) > 0
    
    # Only revise if we have failures AND not pending repair work
    # (pending repair means we should observe first)
    pending_repair = state.data.get("pending_repair", 0) > 0
    
    return has_failures and not pending_repair


def tests_passing(state: State) -> bool:
    """Check if tests are passing and coverage target met."""
    # Only active if we have actually run tests and they're all passing
    has_run_tests = "test_results" in state.data
    no_failures = not state.data.get("failing_nodeids", [])
    coverage_met = state.data.get("branch_cov", 0.0) >= state.data.get("target_branch", 0.0)
    
    return has_run_tests and no_failures and coverage_met


def get_testwriter_contexts_v2() -> List[Context]:
    """Get simplified V2 contexts for the testwriter agent - core refinement loop."""
    
    return [
        # Initial setup phase
        Context(
            id="repo_ready",
            label="Repository Ready",
            is_valid=repo_ready,
            resources=["filesystem"],
            actions=[RunPytestCov()]
        ),
        
        Context(
            id="coverage_baselined", 
            label="Coverage Baselined",
            is_valid=coverage_baselined,
            resources=["filesystem", "analysis"],  # Add filesystem to connect with repo_ready
            actions=[PickTargets()]
        ),
        
        Context(
            id="targets_chosen",
            label="Targets Chosen", 
            is_valid=targets_chosen,
            resources=["analysis", "filesystem", "llm"],
            actions=[IntrospectApi(), SimpleGenerateTestAction()]
        ),
        
        # Core refinement triangle: Tests Generated ↔ Needs Observing ↔ Needs Revising
        Context(
            id="tests_generated",
            label="Tests Generated",
            is_valid=tests_generated,
            resources=["filesystem"],
            actions=[]  # Transition state
        ),
        
        Context(
            id="needs_observing",
            label="Needs Observing",
            is_valid=needs_observing,
            resources=["pytest", "filesystem"],
            actions=[RerunPytestCov()]  # Observe by running tests (rerun version)
        ),
        
        Context(
            id="needs_revising", 
            label="Needs Revising",
            is_valid=needs_revising,
            resources=["llm", "filesystem"],
            actions=[ReasonAboutFailureAction(), ActOnFailureAction(), ObserveRepairAction(), ReviseRepairAction()]
        ),
        
        # Goal state
        Context(
            id="tests_passing",
            label="Tests Passing",
            is_valid=tests_passing,
            resources=["filesystem"],  # Add filesystem to connect with refinement loop
            actions=[]  # Terminal state
        )
    ]
