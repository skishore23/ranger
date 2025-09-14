"""CLI interface for test-writer agent."""

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from core.state.store import get_state
from core.engine.scheduler import run
from core.observe.log import emit
from core.observe.viewer import render_png
from .contexts_v2 import get_testwriter_contexts_v2
from .config import create_config, detect_repo_type, validate_config, get_test_directory


app = typer.Typer(name="testwriter", help="Autonomous test generation agent")


def create_goal_checker(coverage_target: float):
    """Create goal checker with configurable coverage target."""
    def is_goal_reached(state) -> bool:
        """Check if test generation goal is reached."""
        # Basic requirements
        if not ("test_results" in state.data and state.data["test_results"].get("passed", 0) > 0):
            return False
            
        # Check if coverage target is met
        if state.data.get("coverage_improvement", 0) < coverage_target:
            return False
            
        # For comprehensive testing: check if we've tested all available functions
        api_info = state.data.get("api_info", {})
        generated_tests = state.data.get("generated_tests", {})
        
        if not api_info:
            return True  # No API info, can't do more
            
        # Count total testable functions vs generated tests
        total_functions = 0
        for module_path, module_api in api_info.items():
            if "error" not in module_api:
                functions = module_api.get("functions", [])
                total_functions += len([f for f in functions if isinstance(f, dict) and f.get("name")])
        
        passing_tests = len([t for t in generated_tests.values() 
                           if isinstance(t, dict) and t.get("status") == "passing"])
        
        # Goal: test at least 80% of functions OR meet coverage target
        function_coverage_ratio = passing_tests / max(total_functions, 1)
        return function_coverage_ratio >= 0.8 or state.data.get("coverage_improvement", 0) >= coverage_target
        
    return is_goal_reached


@app.command()
def run_testwriter(
    repo: str = typer.Option(".", help="Repository path to analyze"),
    max_ticks: int = typer.Option(500, help="Maximum execution ticks"),
    output_dir: str = typer.Option("./testwriter_output", help="Output directory for results"),
    coverage_target: float = typer.Option(20.0, help="Coverage improvement target (percentage points)"),
    repo_type: Optional[str] = typer.Option(None, help="Repository type (ranger, django, fastapi, package)")
) -> None:
    """Run the test-writer agent on a repository."""
    
    # Load environment variables from .env file
    load_dotenv()
    
    repo_path = Path(repo).resolve()
    output_path = Path(output_dir).resolve()
    
    # Create repository-specific configuration
    config = create_config(repo_path, repo_type)
    
    # Validate configuration
    config_issues = validate_config(config, repo_path)
    if config_issues:
        print(f"❌ Configuration issues found:")
        for issue in config_issues:
            print(f"   - {issue}")
        print(f"💡 Detected repo type: {detect_repo_type(repo_path) or 'unknown'}")
        print(f"💡 Available repo types: ranger, django, fastapi, package")
        raise typer.Exit(1)
    
    print(f"🔧 Repository configuration:")
    print(f"   📁 Type: {config.repo_config.name}")
    print(f"   📂 Source dirs: {config.repo_config.source_dirs}")
    print(f"   🧪 Test dirs: {config.repo_config.test_dirs}")
    print(f"   📊 Coverage targets: {config.repo_config.coverage_targets}")
    
    # Validate repository
    if not repo_path.exists():
        typer.echo(f"Error: Repository path {repo_path} does not exist", err=True)
        raise typer.Exit(1)
    
    # Check for Python project
    if not (repo_path / "pyproject.toml").exists() and not (repo_path / "setup.py").exists():
        typer.echo(f"Error: No Python project found in {repo_path}", err=True)
        raise typer.Exit(1)
    
    # Create output directory
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize fresh events log for this run in output directory
    events_log_path = output_path / "events.jsonl"
    with open(events_log_path, "w") as f:
        pass  # Clear the file for fresh run
    
    # Initialize fresh audit log for detailed debugging
    audit_log_path = output_path / "audit.jsonl"
    with open(audit_log_path, "w") as f:
        pass  # Clear the file for fresh run
    
    # Initialize audit logging for debugging
    from core.observe.audit_log import init_audit_logger
    init_audit_logger(audit_log_path)
    
    # Create custom logger that writes to output directory
    def custom_emit(event) -> None:
        """Custom emit function that writes to output directory events.jsonl"""
        from core.observe.log import emit
        # Call the original emit for console output (but modify it to not write to logs/)
        import sys
        from core.state.types import Event
        
        # Clean topological logging for real-time feedback (copied from emit)
        if event.action.endswith(":start"):
            base_action = event.action.replace(":start", "")
            print(f"   🔄 {event.ctx.upper()}: Starting {base_action}")
        elif event.action.endswith(":reason"):
            print(f"      💭 Reasoning...")
        elif event.action.endswith(":act"):
            print(f"      ⚡ Acting...")
        elif event.action.endswith(":observe"):
            print(f"      👁️  Observing...")
        elif event.action.endswith(":success"):
            print(f"      ✅ Success!")
        elif event.action.endswith(":end"):
            base_action = event.action.replace(":end", "")
            print(f"   ✨ {event.ctx.upper()}: Completed {base_action}")
        elif ":" not in event.action:
            # Regular actions (non-ReAct)
            if event.status == "ok":
                print(f"   ✅ {event.ctx.upper()}: {event.action} completed")
            elif event.status == "noop":
                print(f"   ⚪ {event.ctx.upper()}: {event.action} (no changes)")
            elif event.status == "fail":
                print(f"   ❌ {event.ctx.upper()}: {event.action} failed - {event.notes}")
        
        # Write to output directory events.jsonl
        with open(events_log_path, "a") as f:
            f.write(event.model_dump_json() + "\n")
    
    # Get configured test directory relative to output directory
    test_dir = output_path / "tests" / config.repo_config.output_subdir
    
    # Pure topological: No global repair cache needed - bounded ReAct handles attempts
    
    # Initialize state with repo information and configuration
    state = get_state()
    state.data["repo_path"] = str(repo_path)
    state.data["output_dir"] = str(output_path)
    state.data["test_directory"] = str(test_dir)
    state.data["coverage_target"] = coverage_target
    state.data["venv_active"] = "VIRTUAL_ENV" in os.environ or sys.prefix != sys.base_prefix
    state.data["config"] = {
        "repo_type": config.repo_config.name,
        "source_dirs": config.repo_config.source_dirs,
        "coverage_targets": config.repo_config.coverage_targets,
        "test_runner": config.repo_config.test_runner,
        "import_prefix": config.repo_config.import_prefix,
        "max_tests_per_run": config.max_tests_per_run,
        "max_repair_attempts": config.max_repair_attempts
    }
    
    # Clear and register actions
    # Pure topological: No global registry needed - contexts own their actions
    
    # Get V2 simplified contexts
    contexts = get_testwriter_contexts_v2()
    
    # Setup rendering
    def render() -> None:
        """Render current state as PNG with enhanced visualization."""
        try:
            from core.observe.viewer import render_png
            
            # Extract visualization data from state (safe access)
            execution_path = state.meta.get("execution_path", []) if state.meta else []
            edge_visits = state.meta.get("edge_visits", {}) if state.meta else {}
            node_visits = state.meta.get("node_visits", {}) if state.meta else {}
            guard_nodes = state.meta.get("guard_nodes", set()) if state.meta else set()
            
            render_png(
                state, 
                contexts, 
                output_path / "state_graph.png",
                path=execution_path,
                edge_visits=edge_visits,
                node_visits=node_visits,
                guard_nodes=guard_nodes,
                pin_start="coverage_baselined",  # V2: Pin baseline near start
                pin_goal="tests_passing"        # V2: Pin goal as exit
            )
        except Exception as e:
            typer.echo(f"Warning: Failed to render PNG: {e}", err=True)
    
    typer.echo(f"🚀 Starting test-writer agent on {repo_path}")
    typer.echo(f"📊 Output directory: {output_path}")
    typer.echo(f"🎯 Goal: Generate tests with ≥{coverage_target}pp coverage improvement")
    
    # Create goal checker with target
    is_goal_reached = create_goal_checker(coverage_target)
    
    # Run the agent
    try:
        # Run the scheduler with repair system and custom logger
        stats = run(
            state=state,
            contexts=contexts,
            is_goal=is_goal_reached,
            render=render,
            max_ticks=max_ticks,
            logger=custom_emit
        )
        
        # Report execution stats
        typer.echo(f"📊 Execution stats: {stats.ticks} ticks, {stats.steps_ok} ok, {stats.steps_fail} fail, {stats.steps_noop} noop")
        
        # Report results
        if is_goal_reached(state):
            typer.echo("✅ Test generation completed successfully!")
            if "coverage_improvement" in state.data:
                improvement = state.data["coverage_improvement"]
                typer.echo(f"📈 Coverage improvement: {improvement:.1f}pp (target: {coverage_target}pp)")
        else:
            typer.echo("⚠️  Test generation did not reach target goal")
            
        # Show final state
        typer.echo(f"📋 Final state keys: {list(state.data.keys())}")
        
        # Generate final report
        report_path = output_path / "REPORT.md"
        generate_report(state, report_path)
        typer.echo(f"📄 Report generated: {report_path}")
        typer.echo(f"📋 Events log: {events_log_path}")
        
    except KeyboardInterrupt:
        typer.echo("\n🛑 Test generation interrupted by user")
        raise typer.Exit(130)
    except Exception as e:
        typer.echo(f"❌ Error during test generation: {e}", err=True)
        raise typer.Exit(1)


def generate_report(state, report_path: Path) -> None:
    """Generate markdown report of test generation results."""
    
    content = ["# Test Generation Report\n"]
    
    # Coverage information
    if "baseline_coverage" in state.data:
        baseline = state.data["baseline_coverage"]
        content.append("## Baseline Coverage\n")
        content.append(f"- Overall line rate: {baseline.get('overall_line_rate', 0):.1%}")
        content.append(f"- Overall branch rate: {baseline.get('overall_branch_rate', 0):.1%}\n")
    
    # Target modules
    if "target_modules" in state.data:
        targets = state.data["target_modules"]
        content.append("## Target Modules\n")
        for target in targets[:5]:  # Top 5
            if isinstance(target, dict):
                path = target.get("path", "unknown")
                score = target.get("score", 0)
                content.append(f"- `{path}` (score: {score:.2f})")
        content.append("")
    
    # Generated tests
    if "generated_tests" in state.data:
        tests = state.data["generated_tests"]
        content.append("## Generated Tests\n")
        if isinstance(tests, dict):
            for test_file, info in tests.items():
                content.append(f"- `{test_file}`")
                if isinstance(info, dict) and "invariant" in info:
                    content.append(f"  - Invariant: {info['invariant']}")
        content.append("")
    
    # Test results
    if "test_results" in state.data:
        results = state.data["test_results"]
        content.append("## Test Results\n")
        if isinstance(results, dict):
            passed = results.get("passed", 0)
            failed = results.get("failed", 0)
            content.append(f"- Passed: {passed}")
            content.append(f"- Failed: {failed}")
        content.append("")
    
    # Coverage improvement
    if "coverage_improvement" in state.data:
        improvement = state.data["coverage_improvement"]
        content.append("## Coverage Improvement\n")
        content.append(f"- Improvement: {improvement:.1f} percentage points\n")
    
    # Write report
    with open(report_path, 'w') as f:
        f.write('\n'.join(content))


if __name__ == "__main__":
    app()
