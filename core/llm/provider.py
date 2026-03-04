"""LLM provider protocol and implementations.

This module defines the pluggable LLM provider interface and provides
implementations backed by remote APIs or topology regions.
"""

from __future__ import annotations
import logging

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

from topology.registry import get_region

logger = logging.getLogger(__name__)


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
        """Generate text using the LLM provider.
        
        Args:
            system: Optional system prompt
            prompt: User prompt
            model: Model identifier
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            schema: Optional JSON schema for structured output
            
        Returns:
            Generated text response
        """
        ...


class RegionBackedProvider:
    """Provider that forwards generation requests to a topology region."""

    def __init__(
        self,
        region_key: str,
        *,
        budget: Optional[Dict[str, Any]] = None,
        default_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.region_key = region_key
        self.budget = dict(budget or {})
        self.default_options = dict(default_options or {})

    def generate(
        self,
        *,
        system: Optional[str] = None,
        prompt: str,
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        region = get_region(self.region_key)
        if region is None:
            raise RuntimeError(f"LLM region '{self.region_key}' not registered")

        options: Dict[str, Any] = dict(self.default_options)
        if system:
            options.setdefault("system_prompt", system)
        if model:
            options.setdefault("model", model)
        if temperature is not None:
            options.setdefault("temperature", temperature)
        if max_tokens is not None:
            options.setdefault("max_tokens", max_tokens)

        payload: Dict[str, Any] = {"text": prompt}
        if schema is not None:
            payload["schema"] = schema
        if options:
            payload["options"] = options

        budget = dict(self.budget)
        if max_tokens is not None:
            budget.setdefault("max_tokens", max_tokens)

        atoms = list(region.infer(payload, window=[], budget=budget))
        if not atoms:
            raise RuntimeError(f"LLM region '{self.region_key}' returned no atoms")

        content = atoms[0].content
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return json.dumps(content)
        return json.dumps(content, default=str)


class OpenAIProvider:
    """OpenAI LLM provider implementation - direct API calls."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize OpenAI provider.
        
        Args:
            api_key: OpenAI API key, uses OPENAI_API_KEY env var if None
        """
        try:
            from openai import OpenAI
            
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
            
            self.client = OpenAI(api_key=key)
        except ImportError:
            raise ImportError("openai package required: pip install openai")
    
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
        """Generate text using OpenAI API.
        
        Args:
            system: Optional system prompt
            prompt: User prompt
            model: OpenAI model identifier
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            schema: Optional JSON schema for structured output
            
        Returns:
            Generated text response
        """
        start_time = time.time()
        logger.debug(f"      🤖 OpenAI {model}")
        
        # Build messages list
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        # Build request parameters
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature or 0.2,
            "max_tokens": max_tokens or 1500
        }
        
        # Add JSON mode if requested
        if schema:
            request_params["response_format"] = {"type": "json_object"}
            # OpenAI requires the word "json" in messages when using JSON mode
            if not any("json" in str(msg.get("content", "")).lower() for msg in messages):
                messages.append({"role": "system", "content": "Please respond with valid JSON."})
        
        # Make API call
        response = self.client.chat.completions.create(**request_params)
        
        elapsed = time.time() - start_time
        content = response.choices[0].message.content or ""
        
        # Log result
        success = len(content.strip()) > 0
        status = "✅ Got response" if success else "❌ Empty response"
        logger.debug(f"      {status} ({elapsed:.1f}s)")
        
        if not success:
            logger.debug(f"      ⚠️  Response: {content[:100]}...")
        
        return content


class ClaudeProvider:
    """Claude LLM provider implementation.
    
    Example implementation showing how to create providers for other LLMs.
    Requires anthropic package: pip install anthropic
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude provider.
        
        Args:
            api_key: Anthropic API key, uses ANTHROPIC_API_KEY env var if None
        """
        try:
            import anthropic  # type: ignore[import]
            
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
            
            self.client = anthropic.Anthropic(api_key=key)
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")
    
    def generate(
        self, 
        *, 
        system: Optional[str] = None,
        prompt: str,
        model: str = "claude-3-haiku-20240307",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate text using Claude API."""
        logger.debug(f"      🤖 Claude: {model}")
        
        # Build messages
        messages = [{"role": "user", "content": prompt}]
        
        # Claude API call
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens or 1500,
            temperature=temperature or 0.2,
            system=system or "",
            messages=messages
        )
        
        content = response.content[0].text if response.content else ""
        
        # Note: Claude doesn't have native JSON mode like OpenAI
        # For structured output, you'd need to add JSON parsing instructions to the prompt
        if schema:
            logger.debug("      ⚠️  Claude: JSON schema enforcement via prompt instructions")
        
        return content


@dataclass
class LLMProfile:
    provider: Optional[LLMProvider]
    region_key: Optional[str]
    region_budget: Dict[str, Any]
    region_options: Dict[str, Any]
    defaults: Dict[str, Any]
    region_factory: Optional[Callable[[], Any]]


_LLM_PROFILES: Dict[str, LLMProfile] = {}


def register_llm_profile(
    name: str,
    *,
    provider: Optional[LLMProvider] = None,
    region_key: Optional[str] = None,
    region_budget: Optional[Dict[str, Any]] = None,
    region_options: Optional[Dict[str, Any]] = None,
    defaults: Optional[Dict[str, Any]] = None,
    region_factory: Optional[Callable[[], Any]] = None,
) -> None:
    if provider is None and region_key is None:
        raise ValueError("LLM profile requires either a provider or region_key")

    _LLM_PROFILES[name] = LLMProfile(
        provider=provider,
        region_key=region_key,
        region_budget=dict(region_budget or {}),
        region_options=dict(region_options or {}),
        defaults=dict(defaults or {}),
        region_factory=region_factory,
    )


def resolve_llm_profile(name: str) -> tuple[LLMProvider, Dict[str, Any]]:
    profile = _LLM_PROFILES.get(name)
    if profile is None:
        raise KeyError(f"Unknown LLM profile '{name}'")

    provider = profile.provider
    if profile.region_key:
        region = get_region(profile.region_key)
        if region is None and profile.region_factory is not None:
            profile.region_factory()
            region = get_region(profile.region_key)
        if region is None:
            raise RuntimeError(
                f"LLM profile '{name}' expects region '{profile.region_key}' to be registered"
            )
        provider = RegionBackedProvider(
            profile.region_key,
            budget=profile.region_budget,
            default_options=profile.region_options,
        )
    if provider is None:
        raise RuntimeError(f"LLM profile '{name}' has no provider or region")

    return provider, dict(profile.defaults)


def list_llm_profiles() -> Dict[str, Dict[str, Any]]:
    return {
        name: {
            "region_key": profile.region_key,
            "defaults": dict(profile.defaults),
        }
        for name, profile in _LLM_PROFILES.items()
    }


def clear_llm_profiles() -> None:
    _LLM_PROFILES.clear()


__all__ = [
    "LLMProvider",
    "RegionBackedProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "register_llm_profile",
    "resolve_llm_profile",
    "list_llm_profiles",
    "clear_llm_profiles",
]
