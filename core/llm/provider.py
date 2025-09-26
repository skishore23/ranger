"""LLM Provider Protocol and implementations.

This module defines the pluggable LLM provider interface and provides
implementations for various LLM providers (OpenAI, Claude, etc.).
Users can easily swap providers by changing a single parameter.
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Protocol
import os
import time


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
        print(f"      🤖 OpenAI {model}")
        
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
        print(f"      {status} ({elapsed:.1f}s)")
        
        if not success:
            print(f"      ⚠️  Response: {content[:100]}...")
        
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
            import anthropic
            import os
            
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
        print(f"      🤖 Claude: {model}")
        
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
            print("      ⚠️  Claude: JSON schema enforcement via prompt instructions")
        
        return content


class GroqProvider:
    """Groq LLM provider implementation.
    
    Example implementation for Groq's fast inference API.
    Requires groq package: pip install groq
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Groq provider.
        
        Args:
            api_key: Groq API key, uses GROQ_API_KEY env var if None
        """
        try:
            from groq import Groq
            import os
            
            key = api_key or os.environ.get("GROQ_API_KEY")
            if not key:
                raise ValueError("GROQ_API_KEY environment variable not set")
            
            self.client = Groq(api_key=key)
        except ImportError:
            raise ImportError("groq package required: pip install groq")
    
    def generate(
        self, 
        *, 
        system: Optional[str] = None,
        prompt: str,
        model: str = "llama3-8b-8192",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate text using Groq API."""
        print(f"      🤖 Groq: {model}")
        
        # Build messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        # Groq API call (OpenAI-compatible)
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature or 0.2,
            max_tokens=max_tokens or 1500,
        )
        
        content = response.choices[0].message.content or ""
        
        # Note: Groq may not support JSON mode for all models
        if schema:
            print("      ⚠️  Groq: JSON schema enforcement via prompt instructions")
        
        return content


class LocalLLMProvider:
    """Local LLM provider implementation.
    
    Example implementation for local LLMs via ollama or similar.
    Requires requests package for HTTP calls.
    """
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        """Initialize local LLM provider.
        
        Args:
            base_url: Base URL for local LLM API (e.g., ollama)
        """
        self.base_url = base_url.rstrip("/")
    
    def generate(
        self, 
        *, 
        system: Optional[str] = None,
        prompt: str,
        model: str = "llama2",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate text using local LLM API."""
        print(f"      🤖 Local LLM: {model}")
        
        try:
            import requests
        except ImportError:
            raise ImportError("requests package required: pip install requests")
        
        # Build prompt with system message
        full_prompt = prompt
        if system:
            full_prompt = f"System: {system}\n\nUser: {prompt}"
        
        # Call local API (ollama format)
        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "temperature": temperature or 0.2,
                "num_predict": max_tokens or 1500,
            }
        }
        
        response = requests.post(f"{self.base_url}/api/generate", json=payload)
        response.raise_for_status()
        
        result = response.json()
        content = result.get("response", "")
        
        if schema:
            print("      ⚠️  Local LLM: JSON schema enforcement via prompt instructions")
        
        return content
