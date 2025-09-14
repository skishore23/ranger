"""Functional pytest action demonstrating pure morphisms."""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from pathlib import Path
from core.state.types import State, Delta, JSONValue
from core.action.base import Action
from core.action.safe import Either, safe_subprocess, safe_file_read, safe_json_parse, SafeError


class FunctionalPytestCov(Action):
    """Run pytest with coverage using functional Either patterns."""
    
    name: str = "functional_pytest_cov"
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
        """Execute pytest with functional error handling pipeline."""
        repo_path = Path(str(kwargs["repo_path"]))
        
        # Pure functional pipeline - no exceptions, pure morphisms
        result = (
            self._execute_pytest(repo_path)
            .flat_map(lambda _: self._parse_coverage_files(repo_path))
            .fold(
                left=self._handle_pytest_error,
                right=self._handle_pytest_success
            )
        )
        
        return result
    
    def _execute_pytest(self, repo_path: Path) -> Either[SafeError, None]:
        """Execute pytest command functionally."""
        cmd = [
            "python", "-m", "pytest", 
            "--cov=core",
            "--cov-report=xml",
            "--cov-report=json", 
            "--cov-report=term",
            "-v"
        ]
        
        return (
            safe_subprocess(cmd, cwd=repo_path, timeout=self.timeout_s)
            .flat_map(self._validate_pytest_result)
        )
    
    def _validate_pytest_result(self, process_result) -> Either[SafeError, None]:
        """Validate pytest execution result."""
        if process_result.returncode != 0 and "No module named" in process_result.stderr:
            return Either.Left(SafeError(
                message=f"Missing dependency: {process_result.stderr}",
                error_type="DependencyError",
                context={"stderr": process_result.stderr}
            ))
        return Either.Right(None)
    
    def _parse_coverage_files(self, repo_path: Path) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage files functionally."""
        coverage_xml = repo_path / "coverage.xml"
        coverage_json = repo_path / "coverage.json"
        
        # Parse XML coverage (required)
        xml_result = (
            safe_file_read(coverage_xml)
            .flat_map(self._parse_coverage_xml)
        )
        
        if xml_result.tag == "Left":
            return xml_result
        
        coverage_data = xml_result.right
        
        # Parse JSON coverage (optional enhancement)
        json_result = (
            safe_file_read(coverage_json)
            .flat_map(safe_json_parse)
        )
        
        if json_result.tag == "Right":
            coverage_data["totals"] = json_result.right.get("totals", {})
        
        return Either.Right(coverage_data)
    
    def _parse_coverage_xml(self, xml_content: str) -> Either[SafeError, Dict[str, Any]]:
        """Parse coverage XML content functionally."""
        try:
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
            
            return Either.Right({
                "files": files,
                "overall_line_rate": float(root.get("line-rate", 0)),
                "overall_branch_rate": float(root.get("branch-rate", 0)),
                "timestamp": root.get("timestamp", "")
            })
            
        except Exception as e:
            return Either.Left(SafeError(
                message=f"XML parsing failed: {str(e)}",
                error_type="XMLParseError",
                context={"xml_length": len(xml_content)}
            ))
    
    def _handle_pytest_error(self, error: SafeError) -> Delta:
        """Handle pytest execution errors functionally."""
        # Fail fast - no fallbacks, pure error propagation
        raise RuntimeError(f"Pytest execution failed: {error.message}")
    
    def _handle_pytest_success(self, coverage_data: Dict[str, Any]) -> Delta:
        """Handle successful pytest execution."""
        return {
            "set": {
                "baseline_coverage": coverage_data,
                "pytest_execution": "success"
            }
        }


# Demonstrate functional composition
def compose_test_pipeline(repo_path: Path) -> Either[SafeError, Dict[str, Any]]:
    """Compose entire test pipeline functionally."""
    return (
        safe_subprocess(["python", "-m", "pytest", "--version"], cwd=repo_path)
        .flat_map(lambda _: safe_subprocess([
            "python", "-m", "pytest", 
            "--cov=core", "--cov-report=xml", "-v"
        ], cwd=repo_path))
        .flat_map(lambda _: safe_file_read(repo_path / "coverage.xml"))
        .map(lambda xml: {"coverage_xml": xml, "status": "complete"})
    )
