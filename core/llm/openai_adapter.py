"""OpenAI adapter with strict typing."""

import os
import json
import time
from typing import List, Dict
from openai import OpenAI


class OpenAIAdapter:
    """Adapter for OpenAI API with strict typing."""
    
    def __init__(self) -> None:
        # Explicitly pass the API key to the client
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        self.client = OpenAI(api_key=api_key)
    
    def chat(
        self, 
        messages: List[Dict[str, str]], 
        model: str = "gpt-4o-mini",
        force_json: bool = False,
        temperature: float = 0.5,
        max_tokens: int = 4800,
        task_description: str = "LLM request"
    ) -> str:
        """Send chat completion request and return response content."""
        start_time = time.time()
        
        print(f"      🤖 {task_description}")
        
        # Build request parameters
        request_params = {
            "model": model,
            "messages": messages,  # type: ignore
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        # Add JSON mode if requested
        if force_json:
            request_params["response_format"] = {"type": "json_object"}
        
        response = self.client.chat.completions.create(**request_params)
        
        elapsed = time.time() - start_time
        content = response.choices[0].message.content or ""
        
        # Simple success check based on content length
        success = len(content.strip()) > 0
        status = "✅ Got response" if success else "❌ Empty response"
        
        print(f"      {status} ({elapsed:.1f}s)")
        if not success:
            print(f"      ⚠️  Response: {content[:100]}...")
        
        return content
