#!/usr/bin/env python3
"""
Testwriter Agent Demo

This demonstrates the testwriter agent generating and improving tests
for the Ranger codebase using Ranger's reactive engine principles.
No manual loops - the engine handles everything based on data dependencies.
"""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sdk import Agent
from agents.testwriter.tools import (
    sync_deps,
    index_repo, run_pyright, pick_next_file_llm, trigger_next_file_pick,
    generate_tests, write_tests,
    run_tests, repair_imports_and_deps,
    check_test_failures, analyze_source_code, repair_tests_llm, mark_tests_dirty,
    check_coverage_target, lift_coverage_llm, mark_coverage_dirty,
    format_code,
)
from agents.testwriter.goals import tests_passing_and_covered

# All testwriter capabilities
TOOLS = [
    sync_deps,
    pick_next_file_llm,  # LLM-driven file picker based on coverage
    trigger_next_file_pick,  # Trigger next file selection after tests pass
    index_repo, run_pyright,
    generate_tests, write_tests,
    run_tests, repair_imports_and_deps,
    check_test_failures, analyze_source_code, repair_tests_llm, mark_tests_dirty,
    check_coverage_target, lift_coverage_llm, mark_coverage_dirty,
    format_code,
]

def main():
    print("🧪 Starting Testwriter Agent Demo")
    print(f"📋 Tools: {len(TOOLS)} capabilities")
    print("🎯 Goal: Generate passing tests with 80% coverage")
    print("🔄 Using Ranger's reactive engine - no manual loops!")
    
    # Create agent with all tools
    agent = Agent(TOOLS)
    
    # Let Ranger's engine handle everything automatically
    # The engine will:
    # 1. Setup environment (load_env, sync_deps)
    # 2. Index repository (index_repo, run_pyright)  
    # 3. Generate tests (generate_tests)
    # 4. Write and run tests (write_tests, run_tests)
    # 5. If tests fail: repair_tests_llm updates tests.gen → triggers write_tests again
    # 6. If coverage low: lift_coverage_llm adds more tests → triggers write_tests again
    # 7. Continue until tests_passing_and_covered goal is met
    # LLM-driven intelligent file selection based on coverage analysis
    print("🎯 Strategy: LLM-driven intelligent file selection based on pytest coverage analysis")
    
    result = agent.run(
        initial={
            "codebase.path": ".",
            "coverage.target": 0.80,  # 80% coverage target
            # Remove target.file to allow multiple files for coverage
        },
        goal=tests_passing_and_covered,
        max_steps=50  # Should be faster with focused approach
    )
    
    print(f"\n🎯 Agent Result: {'✅ SUCCESS' if result.ok else '❌ FAILED'}")
    
    if not result.ok:
        print(f"❌ Blocked: {result.blocker}")
        if hasattr(result, 'error') and result.error:
            print(f"❌ Error: {result.error}")
    else:
        # Show final results
        if result.final.exists('coverage.report'):
            final_coverage = result.final.value('coverage.report').get('total', 0.0)
            print(f"📊 Final coverage: {final_coverage:.1f}%")
        
        if result.final.exists('run.result'):
            test_result = result.final.value('run.result')
            print(f"🧪 Tests passed: {test_result.get('passed', False)}")
        
        print(f"🔄 Total steps executed: {result.steps}")
        print("🎉 Testwriter completed successfully!")

if __name__ == "__main__":
    main()