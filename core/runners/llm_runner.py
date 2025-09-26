"""LLM runner implementation using pluggable providers."""

from __future__ import annotations
from typing import Callable, Dict, Any, Optional
from ..workspace import Snapshot
from ..capability import Capability
from ..llm.provider import LLMProvider


class LLMRunner:
    """Runner for LLM-based capabilities."""
    
    def __init__(
        self,
        provider: LLMProvider,
        model: str = "gpt-4o-mini",
        system: Optional[str] = None,
        template: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        map_fn: Optional[Callable[[Snapshot], Dict[str, Any]]] = None,
    ):
        """Initialize LLM runner.
        
        Args:
            provider: LLM provider instance
            model: Model identifier
            system: System prompt
            template: Jinja2 template for user prompt
            schema: JSON schema for structured output
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            map_fn: Function to map workspace to template variables
        """
        self.provider = provider
        self.model = model
        self.system = system
        self.template = template
        self.schema = schema
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.map_fn = map_fn
    
    def run(self, cap: Capability, snap: Snapshot) -> Dict[str, Any]:
        """Execute LLM capability.
        
        Args:
            cap: Capability being executed
            snap: Current workspace snapshot
            
        Returns:
            Dictionary of field updates
            
        Raises:
            RuntimeError: If LLM returns invalid JSON or writes undeclared fields
        """
        # Prepare template variables
        if self.map_fn:
            template_vars = self.map_fn(snap)
        else:
            template_vars = {k: snap.get(k) for k in cap.reads}
        
        # Render prompt from template
        if self.template:
            try:
                from jinja2 import Template  # type: ignore
                prompt = Template(self.template).render(**template_vars)
            except ImportError:
                # Simple string formatting fallback
                prompt = self.template.format(**template_vars)
        else:
            prompt = str(template_vars)
        
        # Generate response
        response = self.provider.generate(
            system=self.system,
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            schema=self.schema,
        )
        
        # Parse JSON response if schema provided
        if self.schema:
            import json
            try:
                parsed_response = json.loads(response)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"LLM returned invalid JSON: {e}") from e
            
            # If single output field, map the entire parsed response to that field
            write_keys = list(cap.writes)
            if len(write_keys) == 1:
                result = {write_keys[0]: parsed_response}
            else:
                # Multiple outputs - expect the JSON to have matching field names
                result = parsed_response
        else:
            # If no schema, use the first declared write field as the key
            write_keys = list(cap.writes)
            if len(write_keys) == 1:
                result = {write_keys[0]: response}
            else:
                # Multiple writes but no schema - this is ambiguous
                result = {"response": response}
        
        # Enforce write locality - capability can only update declared fields
        illegal = set(result.keys()) - cap.writes
        if illegal:
            raise RuntimeError(f"LLM capability {cap.id} wrote undeclared fields: {sorted(illegal)}")
        
        return result
