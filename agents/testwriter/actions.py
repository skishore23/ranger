"""Test-writer agent actions for autonomous test generation."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Any
from core.state.types import State, Delta, JSONValue
from core.action.base import Action
from core.action.safe import Either, Left, Right, safe_subprocess, safe_file_read, safe_json_parse, SafeError


class RunPytestCov:
    """Run pytest with coverage and parse results."""
    
    name: str = "run_pytest_cov"
    locks: List[str] = ["pytest", "filesystem"]
    timeout_s: int = 120
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if repo is ready and coverage not already measured."""
        return (
            "repo_path" in state.data and 
            "baseline_coverage" not in state.data
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract repo path from state."""
        return {"repo_path": state.data["repo_path"]}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Run pytest with coverage using functional Either patterns."""
        repo_path = Path(str(kwargs["repo_path"]))
        
        cmd = [
            "python", "-m", "pytest", 
            "--cov=core",
            "--cov-report=xml",
            "--cov-report=json", 
            "--cov-report=term",
            "-v"
        ]
        
        # Pure functional pipeline using Either monads
        result = (
            safe_subprocess(cmd, cwd=repo_path, timeout=self.timeout_s)
            .flat_map(self._validate_pytest_result)
            .flat_map(lambda proc_result: self._parse_coverage_files(repo_path, proc_result))
            .fold(
                left=self._handle_error,
                right=self._handle_success
            )
        )
        
        return result
    
    def _validate_pytest_result(self, process_result) -> Either[SafeError, Any]:
        """Validate pytest execution result - fail fast on missing dependencies."""
        if process_result.returncode != 0 and "No module named" in process_result.stderr:
            return Left(SafeError(
                message=f"Missing dependency: {process_result.stderr}",
                error_type="DependencyError",
                context={"stderr": process_result.stderr}
            ))
        return Right(process_result)
    
    def _parse_coverage_files(self, repo_path: Path, process_result) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage files functionally."""
        coverage_xml = repo_path / "coverage.xml"
        coverage_json = repo_path / "coverage.json"
        
        # Parse XML coverage (required)
        xml_result = (
            safe_file_read(coverage_xml)
            .flat_map(self._parse_coverage_xml_content)
        )
        
        if xml_result.tag == "Left":
            return xml_result
        
        coverage_data = xml_result.right
        
        # Add process result info
        coverage_data.update({
            "pytest_output": process_result.stdout,
            "pytest_stderr": process_result.stderr,
            "pytest_returncode": process_result.returncode
        })
        
        # Parse JSON coverage (optional enhancement)
        json_result = (
            safe_file_read(coverage_json)
            .flat_map(safe_json_parse)
        )
        
        if json_result.tag == "Right":
            coverage_data["totals"] = json_result.right.get("totals", {})
        
        return Right(coverage_data)
    
    def _parse_coverage_xml_content(self, xml_content: str) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage XML content functionally."""
        try:
            root = ET.fromstring(xml_content)
            return Right(self._extract_coverage_data(root))
        except Exception as e:
            return Left(SafeError(
                message=f"XML parsing failed: {str(e)}",
                error_type="XMLParseError",
                context={"xml_length": len(xml_content)}
            ))
    
    def _extract_coverage_data(self, root) -> Dict[str, Any]:
        """Extract coverage data from XML root - pure function."""
        files = []
        for package in root.findall(".//package"):
            for class_elem in package.findall("classes/class"):
                filename = class_elem.get("filename", "")
                norm_path = filename if filename.startswith("core/") else f"core/{filename}"
                lines = class_elem.find("lines")
                
                if lines is not None:
                    line_rate = float(class_elem.get("line-rate", 0))
                    branch_rate = float(class_elem.get("branch-rate", 0))
                    
                    files.append({
                        "path": norm_path,
                        "line_rate": line_rate,
                        "branch_rate": branch_rate,
                        "lines_covered": len([l for l in lines.findall("line") if l.get("hits", "0") != "0"]),
                        "lines_total": len(lines.findall("line"))
                    })
        
        return {
            "files": files,
            "overall_line_rate": float(root.get("line-rate", 0)),
            "overall_branch_rate": float(root.get("branch-rate", 0)),
            "timestamp": root.get("timestamp", "")
        }
    
    def _handle_error(self, error: SafeError) -> Delta:
        """Handle errors functionally - fail fast, no fallbacks."""
        raise RuntimeError(f"Pytest execution failed: {error.message}")
    
    def _handle_success(self, coverage_data: Dict[str, Any]) -> Delta:
        """Handle successful execution."""
        return {
            "set": {
                "baseline_coverage": coverage_data
            }
        }
    


class PickTargets:
    """Pick target modules for test generation based on coverage and complexity."""
    
    name: str = "pick_targets"
    locks: List[str] = ["analysis"]
    timeout_s: int = 30
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if coverage baseline exists and targets not chosen."""
        return (
            "baseline_coverage" in state.data and
            "target_modules" not in state.data
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract coverage data from state."""
        return {"coverage": state.data["baseline_coverage"]}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Pick target modules based on coverage gaps and complexity."""
        coverage = kwargs["coverage"]
        
        if not isinstance(coverage, dict) or "files" not in coverage:
            return None
        
        files = coverage["files"]
        
        # Score files by (1 - coverage) * complexity_weight
        scored_files = []
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
                
            path = file_info.get("path", "")
            line_rate = file_info.get("line_rate", 1.0)
            branch_rate = file_info.get("branch_rate", 1.0)
            
            # Skip __init__.py and non-relevant files
            if "__init__.py" in path or not path.endswith(".py"):
                continue
            
            # Consider all core modules for function-based testing

            # Prefer core engine and context modules
            complexity_weight = 1.0
            if "engine/" in path:
                complexity_weight = 3.0
            elif "context/" in path:
                complexity_weight = 2.5
            elif "observe/" in path:
                complexity_weight = 2.0
            
            # Combined score: coverage gap * complexity
            coverage_gap = (1 - line_rate) + (1 - branch_rate) * 0.5
            score = coverage_gap * complexity_weight
            
            scored_files.append({
                "path": path,
                "score": score,
                "line_rate": line_rate,
                "branch_rate": branch_rate,
                "complexity_weight": complexity_weight
            })
        
        # Sort by score descending and take top 2 modules for focused testing
        scored_files.sort(key=lambda x: x["score"], reverse=True)
        target_modules = scored_files[:2]  # Limit to 2 modules for focused testing
        
        return {
            "set": {
                "target_modules": target_modules,
                "selection_criteria": {
                    "max_targets": len(scored_files),  # All modules
                    "engine_weight": 3.0,
                    "context_weight": 2.5,
                    "observe_weight": 2.0
                }
            }
        }


class IntrospectApi:
    """Introspect target modules to collect public API information."""
    
    name: str = "introspect_api"
    locks: List[str] = ["analysis", "filesystem"]
    timeout_s: int = 60
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if targets chosen and API not introspected."""
        return (
            "target_modules" in state.data and
            "api_info" not in state.data
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract target modules and repo path."""
        return {
            "target_modules": state.data["target_modules"],
            "repo_path": state.data["repo_path"]
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Introspect public APIs using functional patterns."""
        target_modules = kwargs["target_modules"]
        repo_path = Path(str(kwargs["repo_path"]))
        
        if not isinstance(target_modules, list):
            return None
        
        # Use functional traverse to process all modules
        from core.action.safe import traverse_either
        
        result = (
            traverse_either(target_modules, lambda module: self._introspect_module(module, repo_path))
            .fold(
                left=self._handle_introspection_error,
                right=self._handle_introspection_success
            )
        )
        
        return result
    
    def _introspect_module(self, module_info: Any, repo_path: Path) -> Either[SafeError, tuple[str, Dict[str, Any]]]:
        """Introspect a single module functionally."""
        if not isinstance(module_info, dict):
            return Left(SafeError(
                message="Invalid module info format",
                error_type="ValidationError",
                context={"module_info": str(module_info)}
            ))
        
        module_path = module_info.get("path", "")
        if not module_path:
            return Left(SafeError(
                message="Missing module path",
                error_type="ValidationError", 
                context={"module_info": module_info}
            ))
        
        full_path = repo_path / module_path
        
        return (
            safe_file_read(full_path)
            .flat_map(self._parse_module_ast)
            .map(lambda api_data: (module_path, api_data))
        )
    
    def _parse_module_ast(self, content: str) -> Either[SafeError, Dict[str, Any]]:
        """Parse module AST functionally."""
        try:
            import ast
            tree = ast.parse(content)
            
            functions = []
            classes = []
            
            # Extract all top-level functions (including private ones that might be testable)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    functions.append({
                        "name": node.name,
                        "args": [arg.arg for arg in node.args.args],
                        "lineno": node.lineno
                    })
                elif isinstance(node, ast.ClassDef) and not node.name.startswith('_'):
                    methods = []
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and not item.name.startswith('_'):
                            methods.append({
                                "name": item.name,
                                "args": [arg.arg for arg in item.args.args],
                                "lineno": item.lineno
                            })
                    
                    classes.append({
                        "name": node.name,
                        "methods": methods,
                        "lineno": node.lineno
                    })
            
            return Right({
                "functions": functions,
                "classes": classes,
                "line_count": len(content.splitlines())
            })
            
        except Exception as e:
            return Left(SafeError(
                message=f"AST parsing failed: {str(e)}",
                error_type="ASTParseError",
                context={"content_length": len(content)}
            ))
    
    def _handle_introspection_error(self, error: SafeError) -> Delta:
        """Handle introspection errors - fail fast."""
        raise RuntimeError(f"API introspection failed: {error.message}")
    
    def _handle_introspection_success(self, module_results: List[tuple[str, Dict[str, Any]]]) -> Delta:
        """Handle successful introspection."""
        api_info = {module_path: api_data for module_path, api_data in module_results}
        return {
            "set": {
                "api_info": api_info
            }
        }


class StartValidation:
    """Start the test validation phase."""
    
    name: str = "start_validation"
    locks: List[str] = ["validation"]
    timeout_s: int = 5
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if tests are generated and ready to start validation."""
        return (
            "generated_tests" in state.data and 
            len(state.data.get("generated_tests", {})) > 0 and
            "validation_started" not in state.data
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """No arguments needed."""
        return {}
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Mark validation as started."""
        print("🔄 Starting test validation phase...")
        return {
            "set": {
                "validation_started": True
            }
        }


class RerunPytestCov:
    """Rerun pytest with coverage to measure improvement."""
    
    name: str = "rerun_pytest_cov"
    locks: List[str] = ["pytest", "filesystem"]
    timeout_s: int = 120
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Run when tests exist and need observation (V2 refinement loop).
        
        Runs when:
        1. Tests have been generated, AND
        2. Either no test results yet OR pending repair work
        """
        has_tests = (
            "generated_tests" in state.data and 
            len(state.data.get("generated_tests", {})) > 0
        )
        
        needs_observation = (
            "test_results" not in state.data or  # First run
            state.data.get("pending_repair", 0) > 0  # After repair
        )
        
        return has_tests and needs_observation
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract repo path and tests directory for generated tests."""
        output_dir = state.data.get("output_dir", "testwriter_output")
        tests_path = f"{output_dir}/tests"  # Point to tests subdirectory
        return {
            "repo_path": state.data["repo_path"],
            "tests_directory": tests_path
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Rerun pytest with coverage using functional patterns."""
        repo_path = Path(str(kwargs["repo_path"]))
        tests_dir = kwargs.get("tests_directory", "")
        test_target = str(tests_dir) if tests_dir else "tests/"
        
        cmd = [
            "python", "-m", "pytest", 
            test_target,
            "--cov=core",
            "--cov-report=xml",
            "--cov-report=json",
            "-v"
        ]
        
        # Functional pipeline for test execution and analysis
        result = (
            safe_subprocess(cmd, cwd=repo_path, timeout=self.timeout_s)
            .flat_map(lambda proc_result: self._analyze_test_results(proc_result, repo_path, test_target, state))
            .fold(
                left=self._handle_test_error,
                right=self._handle_test_success
            )
        )
        
        return result
    
    def _analyze_test_results(self, process_result, repo_path: Path, test_target: str, state: State) -> Either[SafeError, Dict[str, Any]]:
        """Analyze test results functionally."""
        # Parse new coverage
        coverage_result = (
            safe_file_read(repo_path / "coverage.xml")
            .flat_map(lambda xml: self._parse_coverage_xml_content(xml))
        )
        
        new_coverage = coverage_result.fold(
            left=lambda _: {},
            right=lambda data: data
        )
        
        # Calculate improvement
        baseline = state.data.get("baseline_coverage", {})
        baseline_rate = baseline.get("overall_line_rate", 0)
        new_rate = new_coverage.get("overall_line_rate", 0)
        improvement = (new_rate - baseline_rate) * 100
        
        # Parse test results using CLI utility
        try:
            from agents.testwriter.pytest_utils import run_pytest_simple
            test_dir = Path(test_target)
            success, output, test_results = run_pytest_simple(test_dir, repo_path)
            
            passed = test_results["passed"]
            failed = test_results["failed"] 
            errors = test_results["errors"]
            
            # Update test statuses
            generated_tests = state.data.get("generated_tests", {}).copy()
            for test_name, test_info in generated_tests.items():
                if isinstance(test_info, dict) and test_info.get("status") == "generated":
                    test_info["status"] = "passing" if (failed == 0 and errors == 0) else "failing"
            
            # Extract failing nodeids
            failing_nodeids = self._extract_failing_nodeids(output)
            
            return Right({
                "test_results": {
                    "passed": passed,
                    "failed": failed,
                    "errors": errors,
                    "returncode": process_result.returncode,
                    "failure_info": test_results.get("failure_info")
                },
                "generated_tests": generated_tests,
                "new_coverage": new_coverage,
                "coverage_improvement": improvement,
                "pytest_final_output": output,
                "pytest_final_stderr": "",
                "failing_nodeids": failing_nodeids,
                "last_test_run_ts": __import__("time").time(),
                "pending_repair": 0
            })
            
        except Exception as e:
            return Left(SafeError(
                message=f"Test result analysis failed: {str(e)}",
                error_type="TestAnalysisError",
                context={"test_target": test_target}
            ))
    
    def _parse_coverage_xml_content(self, xml_content: str) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage XML content functionally (reuse from RunPytestCov)."""
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_content)
            
            files = []
            for package in root.findall(".//package"):
                for class_elem in package.findall("classes/class"):
                    filename = class_elem.get("filename", "")
                    norm_path = filename if filename.startswith("core/") else f"core/{filename}"
                    lines = class_elem.find("lines")
                    
                    if lines is not None:
                        line_rate = float(class_elem.get("line-rate", 0))
                        branch_rate = float(class_elem.get("branch-rate", 0))
                        
                        files.append({
                            "path": norm_path,
                            "line_rate": line_rate,
                            "branch_rate": branch_rate,
                            "lines_covered": len([l for l in lines.findall("line") if l.get("hits", "0") != "0"]),
                            "lines_total": len(lines.findall("line"))
                        })
            
            return Right({
                "files": files,
                "overall_line_rate": float(root.get("line-rate", 0)),
                "overall_branch_rate": float(root.get("branch-rate", 0)),
                "timestamp": root.get("timestamp", "")
            })
            
        except Exception as e:
            return Left(SafeError(
                message=f"Coverage XML parsing failed: {str(e)}",
                error_type="CoverageParseError",
                context={"xml_length": len(xml_content)}
            ))
    
    def _extract_failing_nodeids(self, output: str) -> List[str]:
        """Extract failing test nodeids from pytest output."""
        import re
        failing_nodeids = []
        for line in output.split('\n'):
            if '::' in line and ('FAILED' in line or 'ERROR' in line):
                match = re.search(r'([\w/._-]+\.py::\w+)', line)
                if match:
                    failing_nodeids.append(match.group(1))
        return failing_nodeids
    
    def _handle_test_error(self, error: SafeError) -> Delta:
        """Handle test execution errors - fail fast."""
        raise RuntimeError(f"Test execution failed: {error.message}")
    
    def _handle_test_success(self, test_data: Dict[str, Any]) -> Delta:
        """Handle successful test execution."""
        return {"set": test_data}


# Pure topological: Actions are now context-owned, no global registry needed
