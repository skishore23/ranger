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
import os
import inspect
from typing import Any, Callable, Dict, List, Optional, Set, Union, Protocol
from .capability import Capability
from .engine import Engine
from .workspace import Snapshot
from .merge import WriteSpec, MergeMode
from .errors import SolveResult
from .runners.python_runner import PythonRunner
from .runners.llm_runner import LLMRunner
from .runners.human_runner import HumanRunner
from .llm.provider import RegionBackedProvider, resolve_llm_profile
from topology.types import Budget


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
    pure: bool,
    runner: Optional[Any] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable], Capability]:
    """Base decorator for creating capabilities.

    Args:
        inputs: Fields this capability reads from workspace
        outputs: Fields this capability writes to workspace
        pure: True for compute-only, False for actions with side effects
        runner: Custom runner (defaults to PythonRunner)
        tags: Optional tags for scheduling/telemetry hints
        write_specs: Optional specifications for how to write fields
        post: Optional post-execution validation function

    Returns:
        Capability that can be used with Agent
    """
    def decorator(fn: Callable[[Snapshot], Optional[Dict[str, Any]]]) -> Capability:
        reads, writes = _normalize_inputs_outputs(inputs, outputs)
        
        # Default runner based on purity
        actual_runner = runner or PythonRunner(fn)
        
        # Default tags based on purity
        actual_tags = set(tags) if tags else set()
        if pure:
            actual_tags.add("compute")
        else:
            actual_tags.add("action")
        
        # Create default write specs and ensure every write key is covered
        default_write_specs: Dict[str, WriteSpec] = dict(write_specs) if write_specs else {}
        for key in writes:
            default_write_specs.setdefault(key, WriteSpec())

        cap_id = name or f"{fn.__module__}.{fn.__name__}"

        return Capability(
            id=cap_id,
            reads=reads,
            writes=writes,
            runner=actual_runner,
            post=post,
            write_specs=default_write_specs,
            tags=actual_tags,
            metadata=dict(metadata) if metadata else None,
        )
    
    return decorator


def step(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable], Capability]:
    """Create a step (pure transform with no side effects).
    
    Args:
        inputs: Fields this step reads from workspace
        outputs: Fields this step writes to workspace
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
        pure=True,
        tags=step_tags,
        write_specs=write_specs,
        post=post,
        name=name,
        metadata=metadata,
    )


def tool(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    tags: Optional[Set[str]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable], Capability]:
    """Create a tool (action that may have side effects).
    
    Args:
        inputs: Fields this tool reads from workspace
        outputs: Fields this tool writes to workspace
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
        pure=False,
        tags=tool_tags,
        write_specs=write_specs,
        post=post,
        name=name,
        metadata=metadata,
    )




def llm(
    *,
    inputs: Optional[Union[List[str], Set[str]]] = None,
    outputs: Optional[Union[List[str], Set[str]]] = None,
    profile: Optional[str] = None,
    model: Optional[str] = None,
    system: Optional[str] = None,
    template: Optional[str] = None,
    schema: Optional[Union[Dict[str, Any], str]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[LLMProvider] = None,
    map: Optional[Callable[[Snapshot], Dict[str, Any]]] = None,
    write_specs: Optional[Dict[str, WriteSpec]] = None,
    region_key: Optional[str] = None,
    region_budget: Optional[Dict[str, Any]] = None,
    region_options: Optional[Dict[str, Any]] = None,
    post: Optional[Callable[[Snapshot, Dict[str, Any]], bool]] = None,
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
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
        actual_provider = provider
        profile_name: Optional[str] = None
        provider_defaults: Dict[str, Any] = {}

        if profile is not None:
            if any(param is not None for param in (provider, region_key, region_budget, region_options)):
                raise ValueError("When using profile=..., do not supply provider/region overrides")
            profile_name = profile
        else:
            if actual_provider is None:
                resolved_key = region_key or os.environ.get("RANGER_DEFAULT_LLM_REGION")
                if not resolved_key:
                    raise ValueError(
                        "LLM tool requires a provider, region_key, or profile"
                    )
                actual_provider = RegionBackedProvider(
                    resolved_key,
                    budget=region_budget,
                    default_options=region_options,
                )
            provider_defaults = {}

        if profile_name is None:
            resolved_model = model or provider_defaults.get("model") or "gpt-4o-mini"
            resolved_system = system or provider_defaults.get("system")
            resolved_temperature = (
                temperature if temperature is not None else provider_defaults.get("temperature")
            )
            resolved_max_tokens = (
                max_tokens if max_tokens is not None else provider_defaults.get("max_tokens")
            )
        else:
            resolved_model = model
            resolved_system = system
            resolved_temperature = temperature
            resolved_max_tokens = max_tokens

        # Parse schema if it's a string
        parsed_schema = None
        if isinstance(schema, str):
            import json
            parsed_schema = json.loads(schema)
        elif schema is not None:
            parsed_schema = schema

        runner = LLMRunner(
            provider=actual_provider,
            model=resolved_model,
            system=resolved_system,
            template=template,
            schema=parsed_schema,
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            map_fn=map,
            profile_name=profile_name,
            profile_defaults=provider_defaults,
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
            post=post,
            name=name,
            metadata=metadata,
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
    name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
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
            callback=fn,
            write_keys=list(outputs or []),
        )

        human_tags = {"human", "action"}
        return _base(
            inputs=inputs,
            outputs=outputs,
            pure=False,
            runner=runner,
            tags=human_tags,
            write_specs=write_specs,
            name=name,
            metadata=metadata,
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
    
    def __init__(self, tools: List[Capability], *, budget: Budget | None = None):
        """Initialize agent with tools.
        
        Args:
            tools: List of tools this agent can execute
            budget: Optional topology budget overrides
        """
        self.engine = Engine(tools, budget=budget)
    
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
