"""LLM runner implementation using pluggable providers."""

from __future__ import annotations

from typing import Callable, Dict, Any, Optional, List

from ..workspace import Snapshot
from ..capability import Capability
from ..llm.provider import LLMProvider, resolve_llm_profile


class SkipLLM(Exception):
    """Signal that an LLM invocation should be skipped."""

    pass


class LLMRunner:
    """Runner for LLM-based capabilities."""
    
    def __init__(
        self,
        provider: Optional[LLMProvider],
        model: Optional[str] = None,
        system: Optional[str] = None,
        template: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        map_fn: Optional[Callable[[Snapshot], Dict[str, Any]]] = None,
        profile_name: Optional[str] = None,
        profile_defaults: Optional[Dict[str, Any]] = None,
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
        self.profile_name = profile_name
        self._profile_defaults = dict(profile_defaults or {})
    
    def run(
        self,
        cap: Capability,
        snap: Snapshot,
        context: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
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
            try:
                template_vars = self.map_fn(snap)
            except SkipLLM:
                return {}
        else:
            template_vars = {k: snap.get(k) for k in cap.reads}

        if context:
            template_vars["context_window"] = [
                getattr(atom, "content", atom) for atom in context
            ]
        
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
        
        provider = self.provider
        profile_defaults = dict(self._profile_defaults)
        if self.profile_name is not None:
            provider, profile_defaults = resolve_llm_profile(self.profile_name)

        if provider is None:
            raise RuntimeError(f"LLM provider not configured for capability {cap.id}")

        model = self.model or profile_defaults.get("model") or "gpt-4o-mini"
        system = self.system or profile_defaults.get("system")
        temperature = (
            self.temperature if self.temperature is not None else profile_defaults.get("temperature")
        )
        max_tokens = (
            self.max_tokens if self.max_tokens is not None else profile_defaults.get("max_tokens")
        )

        print(f"   🔍 DEBUG LLM PROMPT:")
        print(f"   🔍   System: {system}")
        print(f"   🔍   Prompt: {prompt[:500]}...")
        print(f"   🔍   Model: {model}, Temp: {temperature}, Max tokens: {max_tokens}")

        # Generate response
        response = provider.generate(
            system=system,
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
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
