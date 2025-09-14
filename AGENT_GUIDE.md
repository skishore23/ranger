# 🛠️ How to Create an Agent

This guide walks you through creating a new agent using Ranger. We'll build a **File Processing Agent** as an example.

## Step 1: Define Your Domain

First, identify the problem domain and key states:

```python
# Example: File Processing Agent
DOMAIN_STATES = [
    "files_discovered",
    "files_validated", 
    "files_processed",
    "results_generated"
]
```

## Step 2: Create Contexts

Define contexts that represent different phases of your agent's operation:

```python
# contexts.py
from core.context.model import Context
from core.state.types import State

def files_ready(state: State) -> bool:
    """Files are ready for processing."""
    return (
        "input_directory" in state.data and
        "file_list" in state.data and
        len(state.data["file_list"]) > 0
    )

def processing_complete(state: State) -> bool:
    """All files have been processed."""
    processed = state.data.get("processed_files", [])
    total = state.data.get("file_list", [])
    return len(processed) == len(total)

# Define contexts
CONTEXTS = [
    Context(
        id="files_ready",
        label="Files Ready for Processing",
        is_valid=files_ready,
        resources=["filesystem"],
        actions=[ValidateFilesAction(), ProcessFilesAction()]
    ),
    Context(
        id="processing_complete", 
        label="Processing Complete",
        is_valid=processing_complete,
        resources=["filesystem"],
        actions=[GenerateReportAction()]
    )
]
```

## Step 3: Implement Actions

Create actions that perform the actual work:

```python
# actions.py
from core.action.base import Action
from core.state.types import State, Delta, JSONValue
from typing import Dict, List, Optional

class ProcessFilesAction(Action):
    name: str = "process_files"
    locks: List[str] = ["filesystem"]
    timeout_s: int = 300
    
    def pre(self, state: State) -> bool:
        """Can process when files are validated."""
        return (
            "file_list" in state.data and
            state.data.get("files_validated", False)
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract file list from state."""
        return {
            "files": state.data["file_list"],
            "output_dir": state.data.get("output_directory", "./output")
        }
    
    def run(self, state: State, **kwargs) -> Optional[Delta]:
        """Process all files."""
        files = kwargs["files"]
        output_dir = kwargs["output_dir"]
        
        processed_files = []
        for file_path in files:
            # Your processing logic here
            result = self.process_file(file_path, output_dir)
            processed_files.append(result)
        
        return {
            "set": {
                "processed_files": processed_files,
                "processing_complete": True
            }
        }
    
    def process_file(self, file_path: str, output_dir: str) -> Dict[str, Any]:
        """Process a single file - implement your logic here."""
        # Your domain-specific processing
        return {"file": file_path, "status": "processed"}
```

## Step 4: Create the Agent Goal

Define when your agent has completed its mission:

```python
# goal.py
from core.engine.goal import Goal
from core.state.types import State

class FileProcessingGoal(Goal):
    def is_reached(self, state: State) -> bool:
        """Goal reached when all files processed and report generated."""
        return (
            state.data.get("processing_complete", False) and
            state.data.get("report_generated", False)
        )
    
    def description(self) -> str:
        return "Process all files and generate summary report"
```

## Step 5: Create the CLI Interface

```python
# cli.py
import typer
from pathlib import Path
from core.engine.scheduler import run_agent
from .contexts import CONTEXTS
from .goal import FileProcessingGoal

def main(
    input_dir: str = typer.Argument(..., help="Input directory to process"),
    output_dir: str = typer.Option("./output", help="Output directory"),
    max_ticks: int = typer.Option(100, help="Maximum execution ticks")
):
    """Run the file processing agent."""
    
    # Initialize state
    initial_state = {
        "input_directory": input_dir,
        "output_directory": output_dir,
        "file_list": list(Path(input_dir).glob("*.txt"))  # Example
    }
    
    # Run agent
    result = run_agent(
        contexts=CONTEXTS,
        goal=FileProcessingGoal(),
        initial_state=initial_state,
        max_ticks=max_ticks
    )
    
    print(f"Agent completed in {result.ticks} ticks")
    print(f"Goal reached: {result.goal_reached}")

if __name__ == "__main__":
    typer.run(main)
```

## Step 6: Agent Directory Structure

Organize your agent following the established pattern:

```
agents/your_agent/
├── __init__.py
├── cli.py              # Command-line interface
├── contexts.py         # Context definitions
├── actions.py          # Action implementations
├── goal.py            # Goal definition
├── config.py          # Configuration (optional)
└── utils.py           # Helper functions (optional)
```

## Step 7: Advanced Features

### Add LLM Integration

```python
from core.llm.openai_adapter import OpenAIAdapter

class AnalyzeContentAction(Action):
    def run(self, state: State, **kwargs) -> Optional[Delta]:
        content = kwargs["content"]
        
        llm = OpenAIAdapter()
        analysis = llm.chat([
            {"role": "user", "content": f"Analyze this content: {content}"}
        ])
        
        return {"set": {"analysis_result": analysis}}
```

### Add Observability

```python
from core.observe.log import emit, Event

# In your action
emit(Event(
    tick=state.meta.get("tick", 0),
    ctx="processing",
    action="process_files",
    status="ok",
    notes=f"Processed {len(files)} files"
))
```

### Add Visualization

```python
from core.observe.viewer import render_regions_and_path

# Generate visualization
render_regions_and_path(
    contexts=CONTEXTS,
    events=execution_events,
    output_path="./output/agent_graph.png"
)
```

## Step 8: Testing Your Agent

Create tests for your agent components:

```python
# test_agent.py
import pytest
from core.state.types import State
from .contexts import files_ready
from .actions import ProcessFilesAction

def test_files_ready_context():
    state = State(data={"input_directory": "/tmp", "file_list": ["a.txt"]})
    assert files_ready(state) == True
    
    empty_state = State(data={})
    assert files_ready(empty_state) == False

def test_process_files_action():
    action = ProcessFilesAction()
    state = State(data={"file_list": ["test.txt"], "files_validated": True})
    
    assert action.pre(state) == True
    
    args = action.args(state)
    assert "files" in args
    assert args["files"] == ["test.txt"]
```

## Complete Example

Here's a minimal but complete agent:

```python
# agents/file_processor/__init__.py
from .contexts import CONTEXTS
from .goal import FileProcessingGoal

__all__ = ["CONTEXTS", "FileProcessingGoal"]
```

```python
# agents/file_processor/contexts.py
from typing import List
from core.context.model import Context
from core.state.types import State
from .actions import DiscoverFilesAction, ProcessFilesAction, GenerateReportAction

def files_discovered(state: State) -> bool:
    return "file_list" in state.data and len(state.data["file_list"]) > 0

def files_processed(state: State) -> bool:
    return state.data.get("processing_complete", False)

def get_contexts() -> List[Context]:
    return [
        Context(
            id="files_discovered",
            label="Files Discovered",
            is_valid=files_discovered,
            resources=["filesystem"],
            actions=[ProcessFilesAction()]
        ),
        Context(
            id="files_processed",
            label="Files Processed", 
            is_valid=files_processed,
            resources=["filesystem"],
            actions=[GenerateReportAction()]
        )
    ]
```

This guide provides the foundation for building sophisticated agents using Ranger. The key is to think in terms of **contexts** (when things are true) and **actions** (what to do about it), letting the topology drive the execution flow.
