# LLM Provider System Guide

The new Ranger SDK includes a pluggable LLM provider system that makes it incredibly easy to swap between different LLM providers (OpenAI, Claude, Groq, local models, etc.) with zero code changes.

## Quick Start

### Basic Usage

```python
from core.sdk import llm, tool, goal, Agent
from core.llm.provider import OpenAIProvider

# Create an LLM tool
@llm(
    inputs=["input"], 
    outputs=["output"],
    provider=OpenAIProvider(),  # 👈 Just change this line to swap providers!
    model="gpt-4o-mini",
    system="You are a helpful assistant.",
    template="Process this: {{input}}"
)
def process_text(ws): pass

# Use in an agent
agent = Agent([process_text])
result = agent.run(initial={"input": "Hello world"}, goal=my_goal)
```

## Available Providers

### 1. OpenAI Provider
```python
from core.llm.provider import OpenAIProvider

provider = OpenAIProvider()  # Uses existing OpenAIAdapter
# Requires: OPENAI_API_KEY environment variable
```

### 2. Claude Provider  
```python
from core.llm.provider import ClaudeProvider

provider = ClaudeProvider()
# Requires: pip install anthropic + ANTHROPIC_API_KEY
```

### 3. Groq Provider
```python
from core.llm.provider import GroqProvider

provider = GroqProvider()
# Requires: pip install groq + GROQ_API_KEY
```

### 4. Local LLM Provider
```python
from core.llm.provider import LocalLLMProvider

provider = LocalLLMProvider("http://localhost:11434")  # Ollama
# Requires: Local ollama server running
```

## Swapping Providers

The beauty of this system is that **the same agent code works with any provider**:

```python
# Define your agent once
@llm(inputs=["text"], outputs=["summary"], 
     provider=None,  # We'll set this later
     template="Summarize: {{text}}")
def summarize(ws): pass

# Swap providers easily
providers = {
    "openai": OpenAIProvider(),
    "claude": ClaudeProvider(), 
    "groq": GroqProvider(),
    "local": LocalLLMProvider()
}

# Same agent, different provider
for name, provider in providers.items():
    summarize.runner.provider = provider  # Runtime swap!
    agent = Agent([summarize])
    # ... run agent
```

## Creating Custom Providers

Implement the `LLMProvider` protocol:

```python
class MyCustomProvider:
    def generate(self, *, system=None, prompt, model, temperature=None, 
                 max_tokens=None, schema=None) -> str:
        # Your implementation here
        return "Generated response"

# Use it
@llm(provider=MyCustomProvider(), ...)
def my_capability(ws): pass
```

## Advanced Features

### JSON Schema Support
```python
@llm(
    inputs=["data"], 
    outputs=["structured_output"],
    provider=OpenAIProvider(),
    schema={
        "type": "object",
        "properties": {
            "structured_output": {"type": "string"}
        }
    }
)
def extract_data(ws): pass
```

### Template Variables
```python
@llm(
    inputs=["name", "age"], 
    outputs=["greeting"],
    template="Hello {{name}}, you are {{age}} years old!",
    map=lambda ws: {"name": ws.value("name"), "age": ws.value("age")}
)
def greet_person(ws): pass
```

## Error Handling

The system is designed to **fail fast** with clear error messages:

- Missing API keys: `ValueError: OPENAI_API_KEY environment variable not set`
- Missing packages: `ImportError: anthropic package required: pip install anthropic`
- Invalid JSON: `RuntimeError: LLM returned invalid JSON: ...`
- Write violations: `RuntimeError: LLM capability wrote undeclared fields: ['extra_field']`

## Migration from Old System

If you have existing LLM capabilities using the old system:

```python
# Old way (if you had one)
def old_llm_capability(ws):
    # Manual LLM calls
    pass

# New way
@llm(uses=["input"], updates=["output"], provider=OpenAIProvider())
def new_llm_capability(ws): pass
```

## Best Practices

1. **Provider Selection**: Choose based on your needs:
   - OpenAI: Best general performance, JSON mode support
   - Claude: Great for reasoning, longer contexts
   - Groq: Fastest inference for supported models
   - Local: Privacy, cost control, offline usage

2. **Error Handling**: Always handle provider unavailability gracefully

3. **Schema Design**: Use JSON schemas for structured output when possible

4. **Template Design**: Keep templates simple and focused

5. **Testing**: Use mock providers for testing (see examples/provider_swapping_demo.py)

## Examples

See `examples/provider_swapping_demo.py` for a complete working example that demonstrates:
- How to swap providers with zero code changes
- Creating custom mock providers for testing
- Handling provider availability gracefully
- Best practices for agent design

## Key Benefits

✅ **Zero Code Changes**: Same agent works with any provider  
✅ **Fail Fast**: Clear errors when providers unavailable  
✅ **Type Safe**: Full mypy compatibility  
✅ **Extensible**: Easy to add new providers  
✅ **Consistent**: Same interface across all providers  
✅ **Functional**: Follows Ranger's functional architecture principles
