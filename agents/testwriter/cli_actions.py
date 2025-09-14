"""Test-writer specific CLI actions."""

import json
from typing import Dict, List, Optional
from pathlib import Path
from core.state.types import State, Delta, JSONValue
from core.action.base import Action
from core.action.safe import Either, Left, Right, safe_subprocess, safe_json_parse, SafeError


class CoverageCommand(Action):
    """Run coverage analysis and return structured results."""
    
    name: str = "coverage_command"
    locks: List[str] = ["pytest", "coverage"]
    timeout_s: int = 120
    max_retries: int = 1
    allow: bool = True
    
    
    def pre(self, state: State) -> bool:
        """Only available when explicitly requested."""
        return "request_coverage" in state.data
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract paths for coverage analysis."""
        return {
            "repo_path": state.data["repo_path"],
            "test_directory": state.data.get("test_directory", "tests"),
            "coverage_targets": state.data.get("config", {}).get("repo_config", {}).get("coverage_targets", ["core"])
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Run pytest with coverage using functional patterns."""
        repo_path = Path(str(kwargs["repo_path"]))
        test_dir = kwargs.get("test_directory", "tests")
        targets = kwargs.get("coverage_targets", ["core"])
        
        # Skip if we already have recent coverage results
        if "coverage_result" in state.data:
            return None
        
        # Build coverage command
        target_args = [f"--cov={target}" for target in targets]
        cmd = ["python", "-m", "pytest", test_dir] + target_args + ["--cov-report=json", "--cov-report=term-missing", "-q"]
        
        # Functional pipeline
        result = (
            safe_subprocess(cmd, cwd=repo_path, timeout=self.timeout_s)
            .flat_map(lambda proc_result: self._parse_coverage_results(proc_result, repo_path))
            .fold(
                left=self._handle_coverage_error,
                right=self._handle_coverage_success
            )
        )
        
        return result
    
    def _parse_coverage_results(self, process_result, repo_path: Path) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage results functionally."""
        from core.action.safe import safe_file_read
        
        coverage_file = repo_path / "coverage.json"
        coverage_result = (
            safe_file_read(coverage_file)
            .flat_map(safe_json_parse)
        )
        
        coverage_data = coverage_result.fold(
            left=lambda _: {},
            right=lambda data: data
        )
        
        return Right({
            "command": " ".join(process_result.args),
            "returncode": process_result.returncode,
            "stdout": process_result.stdout,
            "stderr": process_result.stderr,
            "success": process_result.returncode == 0,
            "coverage_data": coverage_data
        })
    
    def _handle_coverage_error(self, error: SafeError) -> Delta:
        """Handle coverage execution errors."""
        return {
            "set": {
                "coverage_result": {
                    "command": "unknown",
                    "returncode": -1,
                    "stdout": "",
                    "stderr": error.message,
                    "success": False,
                    "coverage_data": {}
                }
            }
        }
    
    def _handle_coverage_success(self, coverage_data: Dict[str, Any]) -> Delta:
        """Handle successful coverage execution."""
        return {
            "set": {
                "coverage_result": coverage_data
            }
        }


class PytestCommand(Action):
    """Run pytest with structured result parsing."""
    
    name: str = "pytest_command"
    locks: List[str] = ["pytest"]
    timeout_s: int = 120
    max_retries: int = 1
    allow: bool = True
    
    
    def pre(self, state: State) -> bool:
        """Only available when explicitly requested."""
        return "request_pytest" in state.data
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract test paths."""
        return {
            "repo_path": state.data["repo_path"],
            "test_path": state.data.get("test_directory", "tests")
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Run pytest using functional patterns."""
        repo_path = Path(str(kwargs["repo_path"]))
        test_path = kwargs.get("test_path", "tests")
        extra_args = kwargs.get("extra_args", "")
        
        # Skip if we already have recent pytest results
        if "pytest_result" in state.data:
            return None
        
        cmd = ["python", "-m", "pytest", test_path, "--tb=short", "-v"]
        if extra_args:
            cmd.extend(extra_args.split())
        
        # Functional pipeline
        result = (
            safe_subprocess(cmd, cwd=repo_path, timeout=self.timeout_s)
            .map(self._parse_pytest_output)
            .fold(
                left=self._handle_pytest_error,
                right=self._handle_pytest_success
            )
        )
        
        return result
    
    def _parse_pytest_output(self, process_result) -> Dict[str, Any]:
        """Parse pytest output functionally."""
        output = process_result.stdout
        passed = output.count(" PASSED")
        failed = output.count(" FAILED") 
        errors = output.count(" ERROR")
        
        return {
            "command": " ".join(process_result.args),
            "returncode": process_result.returncode,
            "stdout": process_result.stdout,
            "stderr": process_result.stderr,
            "success": process_result.returncode == 0,
            "test_counts": {
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "total": passed + failed + errors
            }
        }
    
    def _handle_pytest_error(self, error: SafeError) -> Delta:
        """Handle pytest execution errors."""
        return {
            "set": {
                "pytest_result": {
                    "command": "unknown",
                    "returncode": -1,
                    "stdout": "",
                    "stderr": error.message,
                    "success": False,
                    "test_counts": {"passed": 0, "failed": 0, "errors": 0, "total": 0}
                }
            }
        }
    
    def _handle_pytest_success(self, pytest_data: Dict[str, Any]) -> Delta:
        """Handle successful pytest execution."""
        return {
            "set": {
                "pytest_result": pytest_data
            }
        }
