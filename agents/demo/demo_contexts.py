"""Demo contexts for topology agent."""

from core.context.model import Context
from core.state.types import State

# Define demo contexts with validation functions
Start = Context(
    "C0",
    "Start",
    lambda s: "url" in s.data,
    resources=["web"]
)

Docs = Context(
    "C1", 
    "DocsReady",
    lambda s: "docs" in s.data,
    resources=["web", "content"]
)

Synthesis = Context(
    "C4",
    "Synthesis", 
    lambda s: "summary" in s.data or "docs" in s.data,
    resources=["content", "ai"]
)

Publish = Context(
    "C6",
    "Publish",
    lambda s: "summary" in s.data and not s.data.get("published"),
    resources=["ai", "fs"]
)

# Export all contexts
CONTEXTS = [Start, Docs, Synthesis, Publish]
