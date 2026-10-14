# LLM Provider Guide

`core.llm.provider.LLMProvider` defines synchronous `generate(*, system=None, prompt, model, temperature=None, max_tokens=None, schema=None) -> str`. Inject a provider into `@llm` to test without any external service. This complete example renders Jinja2, validates JSON Schema and stores the parsed dict under a single declared output:

```python
import json
from core.sdk import llm, goal, Agent
from core.llm.provider import register_llm_profile

class OfflineProvider:
    def generate(self, *, system=None, prompt, model, temperature=None,
                 max_tokens=None, schema=None):
        assert temperature == 0.0
        return json.dumps({"text": f"Hello {prompt}!"})

register_llm_profile("greeting", provider=OfflineProvider(),
                     defaults={"model": "offline", "temperature": 0.0, "max_tokens": 100})

@llm(profile="greeting", inputs=["name"], outputs=["greeting"], template="{{ name }}",
     schema={"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]})
def greet(state):
    pass

@goal(scope=["greeting"])
def done(state):
    return state["greeting"]["text"] == "Hello Ranger!"

result = Agent([greet]).run(initial={"name": "Ranger"}, goal=done)
assert result.ok, result
print(result.final["greeting"]["text"])
```

`map=` can return template variables or raise `core.runners.llm_runner.SkipLLM` to skip generation. The skipped capability consumes its current inputs; changed inputs make it ready again. See the README ReAct example for a dependency-gated answer.

Built-in adapters are `OpenAIProvider`, `ClaudeProvider` and `RegionBackedProvider`. OpenAI uses its SDK and configured credentials; Claude requires the optional `anthropic` package and credentials. There are no shipped Groq or local-server provider classes. A custom implementation can call a local service using the same protocol. It must respect the requested completion ceiling and return text (JSON text when a schema is supplied).

`@llm` requires a provider, `region_key`, configured default region or profile when the decorator is constructed. A profile cannot be combined with explicit provider/region overrides, but model/system/temperature/max_tokens overrides are supported. Profiles resolve lazily; `register_llm_profile` can use a concrete provider as above or a region key plus a factory.

All LLM runner calls and built-in adapters use the registered guard and budget boundary. If a Python capability calls a custom provider directly, route the call through `core.execution.generate_with_policy(provider, ...)`; arbitrary direct `.generate()` calls cannot be intercepted automatically. Guards check the complete request, including system/context/options/schema, and block on errors or failed repairs. Repairs cannot change the original schema. Response validation retains the original schema even if the provider mutates its received copy.

Default budgets reserve conservative full-request UTF-8 bytes + 64 framing units + completion allowance, without refunds. Zero/smaller explicit budgets are authoritative; completion allowance is clamped. Invalid JSON/schema results cannot commit. Explicit zero temperature is preserved. Deadline checks cannot interrupt synchronous provider work. These policy checks do not isolate arbitrary Python. See [API reference](API_REFERENCE.md) for accounting and recording details.
