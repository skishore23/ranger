"""Pytest failure parser for test repair system."""

from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, TypedDict


class FailInfo(TypedDict):
    """Information about a test failure."""
    kind: str        # "compile"|"collect"|"assertion"|"hypothesis"|"timeout"
    nodeid: str
    filename: str
    line: int
    trace_short: str
    snippet: str
    invariant: str   # from header "# Generated test for invariant: ..."


def extract_first_failure(junit_xml: Path, repo_root: Path) -> Optional[FailInfo]:
    """Extract first failure from pytest junit XML output."""
    if not junit_xml.exists():
        return None
    
    try:
        root = ET.parse(junit_xml).getroot()
    except ET.ParseError:
        return None
    
    # Find first test case with failure or error
    case = root.find(".//testcase[failure]")
    if case is None:
        case = root.find(".//testcase[error]")
    if case is None:
        return None
    
    # Extract nodeid
    classname = case.get("classname", "")
    name = case.get("name", "")
    nodeid = f"{classname}::{name}" if classname else name
    
    # Extract failure message
    msg = (case.findtext("failure") or case.findtext("error") or "").strip()
    
    # Try to extract file and line from failure message
    m = re.search(r'File "([^"]+)", line (\d+)', msg)
    if m:
        filename = Path(m.group(1))
        line = int(m.group(2))
    else:
        # Extract test file from classname
        filename = Path(repo_root / (case.get("file") or "unknown.py"))
        line = 1
    
    # Extract code snippet around failure
    snippet = ""
    invariant = ""
    
    if filename.exists():
        try:
            text = filename.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
            
            # Extract invariant from header comment
            header_scan_lines = min(10, len(lines))
            for i in range(header_scan_lines):
                if "Generated test for invariant:" in lines[i]:
                    invariant = lines[i].split("Generated test for invariant:")[-1].strip()
                    break
            
            # Extract snippet around failure line (±7 lines context)
            context_lines = 7
            lo = max(0, line - context_lines)
            hi = min(len(lines), line + context_lines - 2)
            snippet = "\n".join(f"{i+1:3}: {lines[i]}" for i in range(lo, hi))
            
        except Exception:
            snippet = "Could not read file"
    
    # Classify failure type
    kind = "assertion"
    if any(err in msg for err in ["ImportError", "NameError", "SyntaxError", "ModuleNotFoundError"]):
        kind = "compile"
    elif "collection failed" in msg.lower() or "import error" in msg.lower():
        kind = "collect"
    elif "hypothesis" in msg.lower() or "falsifying example" in msg.lower():
        kind = "hypothesis"
    elif "timeout" in msg.lower() or "timed out" in msg.lower():
        kind = "timeout"
    
    # Get short trace (last N lines for concise error context)
    trace_tail_lines = 20
    trace_lines = msg.splitlines()
    trace_short = "\n".join(trace_lines[-trace_tail_lines:])
    
    return FailInfo(
        kind=kind,
        nodeid=nodeid,
        filename=str(filename),
        line=line,
        trace_short=trace_short,
        snippet=snippet,
        invariant=invariant
    )


def run_single_test(test_path: Path, test_function: str, repo_root: Path) -> tuple[bool, str]:
    """Run a single test and return success status and output."""
    import subprocess
    
    nodeid = f"{test_path.relative_to(repo_root)}::{test_function}"
    
    try:
        result = subprocess.run(
            [
                "python", "-m", "pytest", 
                "-q", str(nodeid),
                "--maxfail=1", 
                "--color=no", 
                "--tb=short", 
                "--no-header", 
                "--no-summary"
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30  # Fail fast on slow tests
        )
        
        return result.returncode == 0, result.stdout + result.stderr
        
    except subprocess.TimeoutExpired:
        return False, "Test timed out after 30 seconds"
    except Exception as e:
        return False, f"Failed to run test: {str(e)}"


def run_pytest_with_xml(test_dir: Path, repo_root: Path, xml_output: Path) -> tuple[bool, str]:
    """Run pytest on test directory and generate XML output."""
    import subprocess
    
    try:
        result = subprocess.run(
            [
                "python", "-m", "pytest", 
                "-q", str(test_dir),
                "--maxfail=1",
                "--junitxml", str(xml_output),
                "--color=no"
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120  # Fail fast on long test runs
        )
        
        return result.returncode == 0, result.stdout + result.stderr
        
    except subprocess.TimeoutExpired:
        return False, "Pytest run timed out after 120 seconds"
    except Exception as e:
        return False, f"Failed to run pytest: {str(e)}"


def run_pytest_simple(test_dir: Path, repo_root: Path) -> tuple[bool, str, dict]:
    """Run pytest and parse output directly - simpler than XML."""
    import subprocess
    import re
    
    try:
        result = subprocess.run(
            [
                "python", "-m", "pytest", 
                "-v", str(test_dir),
                "--tb=short",
                "--no-header",
                "--color=no"
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        output = result.stdout + result.stderr
        
        # Parse results from output
        passed = len(re.findall(r'PASSED', output))
        failed = len(re.findall(r'FAILED', output))
        errors = len(re.findall(r'ERROR', output))
        
        # Extract first failure details
        failure_info = None
        if failed > 0 or errors > 0:
            failure_info = extract_failure_from_output(output, test_dir, repo_root)
        
        test_results = {
            "passed": passed,
            "failed": failed, 
            "errors": errors,
            "total": passed + failed + errors,
            "failure_info": failure_info
        }
        
        return result.returncode == 0, output, test_results
        
    except subprocess.TimeoutExpired:
        return False, "Pytest timed out", {"passed": 0, "failed": 0, "errors": 1, "total": 1}
    except Exception as e:
        return False, f"Pytest failed: {e}", {"passed": 0, "failed": 0, "errors": 1, "total": 1}


def extract_failure_from_output(output: str, test_dir: Path, repo_root: Path) -> Optional[FailInfo]:
    """Extract failure info from pytest stdout/stderr - handles both FAILED tests and collection ERRORs."""
    import re
    
    # Look for FAILED test lines first
    failed_match = re.search(r'(\S+\.py::\S+)\s+FAILED', output)
    if failed_match:
        nodeid = failed_match.group(1)
    else:
        # Look for collection ERROR lines
        error_match = re.search(r'ERROR collecting (\S+\.py)', output)
        if not error_match:
            return None
        # Create a fake nodeid for collection errors
        nodeid = f"{error_match.group(1)}::collection_error"
    
    # Extract the failure section
    failure_section = ""
    lines = output.split('\n')
    in_failure = False
    for line in lines:
        if 'FAILURES' in line or 'ERRORS' in line:
            in_failure = True
        elif in_failure and ('=' * 20 in line or 'short test summary' in line.lower()):
            break
        elif in_failure:
            failure_section += line + '\n'
    
    if not failure_section:
        return None
    
    # Extract file and line from traceback
    file_line_match = re.search(r'(\S+\.py):(\d+):', failure_section)
    if file_line_match:
        filename = file_line_match.group(1)
        line = int(file_line_match.group(2))
    else:
        filename = nodeid.split('::')[0] if '::' in nodeid else "unknown.py"
        line = 1
    
    # Classify error type
    kind = "assertion"
    if any(err in failure_section for err in ["NameError", "ImportError", "ModuleNotFoundError"]):
        kind = "compile"
    elif "SyntaxError" in failure_section:
        kind = "compile"
    elif "collection failed" in failure_section.lower():
        kind = "collect"
    
    # Extract snippet from actual file
    snippet = ""
    invariant = ""
    file_path = repo_root / filename
    if file_path.exists():
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            lines_list = text.splitlines()
            
            # Extract invariant from header
            for i, line_text in enumerate(lines_list[:10]):
                if "Generated test for invariant:" in line_text:
                    invariant = line_text.split("Generated test for invariant:")[-1].strip()
                    break
            
            # Extract snippet around failure
            context = 5
            start = max(0, line - context)
            end = min(len(lines_list), line + context)
            snippet = "\n".join(f"{i+1:3}: {lines_list[i]}" for i in range(start, end))
            
        except Exception:
            snippet = "Could not read file"
    
    return FailInfo(
        kind=kind,
        nodeid=nodeid,
        filename=filename,
        line=line,
        trace_short=failure_section.strip(),
        snippet=snippet,
        invariant=invariant
    )
