"""Functional CLI action using Either types for pure morphisms."""

from typing import Dict, List, Optional
from pathlib import Path
from core.state.types import State, Delta, JSONValue
from core.action.base import Action
from core.action.safe import Either, safe_subprocess, SafeError


class FunctionalCliCommand(Action):
    """Execute CLI commands with functional error handling."""
    
    name: str = "functional_cli_command"
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
        """Execute CLI command using functional Either pattern."""
        command = state.data.get("cli_command")
        if not command:
            return None
            
        repo_path = Path(str(kwargs.get("repo_path", ".")))
        
        # Pure functional pipeline using Either monads
        result = (
            safe_subprocess(
                command.split() if isinstance(command, str) else command,
                cwd=repo_path,
                timeout=self.timeout_s
            )
            .map(self._extract_result_data)
            .fold(
                left=self._handle_error,
                right=self._handle_success
            )
        )
        
        return result
    
    def _extract_result_data(self, process_result) -> Dict[str, JSONValue]:
        """Pure function to extract data from subprocess result."""
        return {
            "command": " ".join(process_result.args),
            "returncode": process_result.returncode,
            "stdout": process_result.stdout,
            "stderr": process_result.stderr,
            "success": process_result.returncode == 0
        }
    
    def _handle_error(self, error: SafeError) -> Delta:
        """Pure function to handle errors."""
        return {
            "set": {
                "cli_result": {
                    "command": "unknown",
                    "returncode": -1,
                    "stdout": "",
                    "stderr": error.message,
                    "success": False,
                    "error_type": error.error_type
                }
            }
        }
    
    def _handle_success(self, result_data: Dict[str, JSONValue]) -> Delta:
        """Pure function to handle success."""
        return {
            "set": {
                "cli_result": result_data
            }
        }


# Example of how this enables composition
def compose_cli_actions(commands: List[str]) -> Either[SafeError, List[Dict[str, JSONValue]]]:
    """Compose multiple CLI commands functionally."""
    from core.action.safe import traverse_either
    
    def execute_command(cmd: str) -> Either[SafeError, Dict[str, JSONValue]]:
        return (
            safe_subprocess(cmd.split(), cwd=Path("."))
            .map(lambda proc: {
                "command": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "success": proc.returncode == 0
            })
        )
    
    return traverse_either(commands, execute_command)
