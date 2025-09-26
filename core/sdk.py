"""Ranger SDK - Clean DX interface for topological agents.

This module provides the primary user-facing API for Ranger:
- @step decorator for pure transforms (no side effects)
- @tool decorator for actions (CLI/API/IO/side effects)
- @llm, @human decorators as specialized tools
- @goal decorator for defining completion conditions
- Agent class with .run() method
- Fail-fast design: clear separation between compute and actions
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Set, Union, Protocol
from functools import wraps
import inspect
from .capability import Capability
from .engine import Engine
from .workspace import Snapshot
from .merge import WriteSpec, MergeMode
from .errors import SolveResult
from .runners.python_runner import PythonRunner
from .runners.llm_runner import LLMRunner
from .runners.human_runner import HumanRunner


class LLMProvider(Protocol):
    """Protocol for pluggable LLM providers."""
    def generate(
        self, 
        *, 
        system: Optional[str] = None,
        prompt: str,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate text using the LLM provider."""
        ...


def _normalize_inputs_outputs(
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
) -> tuple[Set[str], Set[str]]:
    """Normalize inputs/outputs to sets."""
    reads = set(inputs) if inputs is not None else set()
    writes = set(outputs) if outputs is not None else set()
    return reads, writes


def _base(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    uses: Optional[Union[List[str], Set[str]]] = None,
    updates: Optional[Union[List[str], Set[str]]] = None,
    pure: bool,
    runner: Optional[Any] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
) -> Callable[[Callable], Capability]:
    """Base decorator for creating capabilities.
    
    Args:
        inputs: Fields this capability reads from workspace
        outputs: Fields this capability writes to workspace
        uses: Alias for inputs (silent compatibility)
        updates: Alias for outputs (silent compatibility)
        pure: True for compute-only, False for actions with side effects
        runner: Custom runner (defaults to PythonRunner)
        tags: Optional tags for scheduling/telemetry hints
        write_specs: Optional specifications for how to write fields
        post: Optional post-execution validation function
    
    Returns:
        Capability that can be used with Agent
    """
    def decorator(fn: Callable[[Snapshot], Optional[Dict[str, Any]]]) -> Capability:
        # Handle aliases with conflict detection
        if inputs is not None and uses is not None and set(inputs) != set(uses):
            raise ValueError("Cannot specify conflicting 'inputs' and 'uses' parameters")
        if outputs is not None and updates is not None and set(outputs) != set(updates):
            raise ValueError("Cannot specify conflicting 'outputs' and 'updates' parameters")
        
        # Use primary names, fall back to aliases
        final_inputs = inputs if inputs is not None else uses
        final_outputs = outputs if outputs is not None else updates
        
        reads, writes = _normalize_inputs_outputs(final_inputs, final_outputs)
        
        # Default runner based on purity
        actual_runner = runner or PythonRunner(fn)
        
        # Default tags based on purity
        actual_tags = set(tags) if tags else set()
        if pure:
            actual_tags.add("compute")
        else:
            actual_tags.add("action")
        
        # Create default write specs if none provided
        default_write_specs = write_specs or {k: WriteSpec() for k in writes}
        
        return Capability(
            id=fn.__name__,
            reads=reads,
            writes=writes,
            runner=actual_runner,
            post=post,
            write_specs=default_write_specs,
            tags=actual_tags,
        )
    
    return decorator


def step(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    uses: Optional[Union[List[str], Set[str]]] = None,
    updates: Optional[Union[List[str], Set[str]]] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
) -> Callable[[Callable], Capability]:
    """Create a step (pure transform with no side effects).
    
    Args:
        inputs: Fields this step reads from workspace
        outputs: Fields this step writes to workspace
        uses: Alias for inputs (silent compatibility)
        updates: Alias for outputs (silent compatibility)
        tags: Optional tags for scheduling/telemetry hints
        write_specs: Optional specifications for how to write fields
        post: Optional post-execution validation function
    
    Returns:
        Capability that can be used with Agent
    """
    step_tags = set(tags) if tags else set()
    step_tags.add("compute")
    
    return _base(
        inputs=inputs,
        outputs=outputs,
        uses=uses,
        updates=updates,
        pure=True,
        tags=step_tags,
        write_specs=write_specs,
        post=post,
    )


def tool(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    uses: Optional[Union[List[str], Set[str]]] = None,
    updates: Optional[Union[List[str], Set[str]]] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
) -> Callable[[Callable], Capability]:
    """Create a tool (action that may have side effects).
    
    Args:
        inputs: Fields this tool reads from workspace
        outputs: Fields this tool writes to workspace
        uses: Alias for inputs (silent compatibility)
        updates: Alias for outputs (silent compatibility)
        tags: Optional tags for scheduling/telemetry hints
        write_specs: Optional specifications for how to write fields
        post: Optional post-execution validation function
    
    Returns:
        Capability that can be used with Agent
    """
    tool_tags = set(tags) if tags else set()
    tool_tags.add("action")
    
    return _base(
        inputs=inputs,
        outputs=outputs,
        uses=uses,
        updates=updates,
        pure=False,
        tags=tool_tags,
        write_specs=write_specs,
        post=post,
    )




def llm(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    model: str = "gpt-4o-mini",
    system: Optional[str] = None,
    template: Optional[str] = None,
    schema: Optional[Union[Dict[str, Any], str]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[LLMProvider] = None,
    map: Optional[Callable[[Snapshot], Dict[str, Any]]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
) -> Callable[[Callable], Capability]:
    """Create an LLM tool (convenience wrapper around @tool).
    
    Args:
        inputs: Fields this LLM reads from workspace
        outputs: Fields this LLM writes to workspace
        model: LLM model to use
        system: System prompt
        template: Jinja2 template for user prompt
        schema: JSON schema for structured output
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        provider: LLM provider instance
        map: Function to map workspace to template variables
        write_specs: Optional specifications for how to write fields
    
    Returns:
        Capability that can be used with Agent
    """
    def decorator(fn: Callable) -> Capability:
        if provider is None:
            raise ValueError("LLM tool requires a provider")
        
        # Parse schema if it's a string
        parsed_schema = None
        if isinstance(schema, str):
            import json
            parsed_schema = json.loads(schema)
        elif schema is not None:
            parsed_schema = schema
        
        runner = LLMRunner(
            provider=provider,
            model=model,
            system=system,
            template=template,
            schema=parsed_schema,
            temperature=temperature,
            max_tokens=max_tokens,
            map_fn=map,
        )
        
        # Use base decorator with LLM runner (LLMs are actions, not pure compute)
        llm_tags = {"llm", "action"}
        return _base(
            inputs=inputs,
            outputs=outputs,
            pure=False,
            runner=runner,
            tags=llm_tags,
            write_specs=write_specs,
        )(fn)
    
    return decorator




def human(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    title: str = "Human Review",
    description: Optional[str] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
) -> Callable[[Callable], Capability]:
    """Create a human collaboration tool (convenience wrapper around @tool).
    
    Args:
        inputs: Fields this human task reads from workspace
        outputs: Fields this human task writes to workspace
        title: Title for the human review card
        description: Description of what human should do
        fields: Form fields for human input
        write_specs: Optional specifications for how to write fields
    
    Returns:
        Capability that can be used with Agent
    """
    def decorator(fn: Callable) -> Capability:
        runner = HumanRunner(
            title=title,
            description=description,
            fields=fields,
        )
        
        # Use base decorator with Human runner (Human collaboration is an action)
        human_tags = {"human", "action"}
        return _base(
            inputs=inputs,
            outputs=outputs,
            pure=False,
            runner=runner,
            tags=human_tags,
            write_specs=write_specs,
        )(fn)
    
    return decorator


def goal(
    scope: Optional[Union[List[str], Set[str]]] = None,
) -> Callable[[Callable], Callable]:
    """Create a goal function with scope metadata.
    
    Args:
        scope: Fields that must exist for goal to be considered
    
    Returns:
        Decorated goal function
    """
    def decorator(fn: Callable[[Snapshot], bool]) -> Callable[[Snapshot], bool]:
        goal_scope = set(scope) if scope is not None else set()
        fn.__ranger_goal_scope__ = goal_scope  # type: ignore
        return fn
    
    return decorator


class Agent:
    """Topological agent that executes tools to reach goals."""
    
    def __init__(self, tools: List[Capability]):
        """Initialize agent with tools.
        
        Args:
            tools: List of tools this agent can execute
        """
        self.engine = Engine(tools)
    
    def run(
        self,
        *,
        initial: Dict[str, Any],
        goal: Callable[[Snapshot], bool],
        max_steps: int = 60,
    ) -> SolveResult:
        """Run the agent to achieve the goal.
        
        Args:
            initial: Initial workspace state
            goal: Function that returns True when goal is achieved
            max_steps: Maximum execution steps
        
        Returns:
            SolveResult with outcome and final state
        """
        return self.engine.solve(
            initial=initial,
            goal=goal,
            max_steps=max_steps,
        )