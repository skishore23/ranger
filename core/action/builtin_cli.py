"""CLI action for running arbitrary commands."""

import subprocess
from typing import Dict, List, Optional
from pathlib import Path
from core.state.types import State, Delta, JSONValue
from core.action.base import Action


class CliCommand(Action):
    """Execute CLI commands with structured output parsing."""
    
    name: str = "cli_command"
    locks: List[str] = ["filesystem"]
    timeout_s: int = 60
    max_retries: int = 1
    allow: bool = True
    
    
    def pre(self, state: State) -> bool:
        """Only available when explicitly requested via cli_command in state."""
        return "cli_command" in state.data and state.data["cli_command"]
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract repo path for command execution."""
        return {
            "repo_path": state.data.get("repo_path", "."),
            "output_dir": state.data.get("output_dir", "./output")
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Execute CLI command and parse results."""
        command = state.data.get("cli_command")
        if not command:
            return None
            
        repo_path = Path(str(kwargs.get("repo_path", ".")))
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout_s
            )
            
            return {
                "set": {
                    "cli_result": {
                        "command": command,
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "success": result.returncode == 0
                    }
                }
            }
            
        except subprocess.TimeoutExpired:
            return {
                "set": {
                    "cli_result": {
                        "command": command,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": "Command timed out",
                        "success": False
                    }
                }
            }
        except Exception as e:
            return {
                "set": {
                    "cli_result": {
                        "command": command,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": str(e),
                        "success": False
                    }
                }
            }


# Test-writer specific CLI actions have been moved to agents/testwriter/
# The core framework only provides the generic CliCommand for arbitrary command execution
