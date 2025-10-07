"""Configuration dataclasses for the deep research agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LLMConfig:
    """LLM configuration for research stages."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.1
    system_prompt: Optional[str] = None
    max_tokens: int = 7000

    def to_state(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "system_prompt": self.system_prompt,
            "max_tokens": self.max_tokens,
        }


@dataclass
class RetrievalConfig:
    """Controls how the agent performs external research."""

    max_queries: int = 8
    sources_per_query: int = 5

    def to_state(self) -> Dict[str, Any]:
        return {
            "max_queries": self.max_queries,
            "sources_per_query": self.sources_per_query,
        }


@dataclass
class DeepResearchConfig:
    """Top-level configuration for the deep research agent."""

    deliverable: str = "Comprehensive research report"
    audience: str = "Executive decision makers"
    depth: str = "doctoral"
    desired_length_pages: int = 15
    min_citations: int = 40
    require_human_feedback: bool = False
    output_path: Optional[Path] = None
    llm: LLMConfig = field(default_factory=LLMConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    custom_directives: List[str] = field(default_factory=lambda: [
        "Embed rigorous citations using author-year and link references.",
        "Surface dissenting viewpoints and methodological limitations.",
        "Quantify findings with data tables where possible.",
    ])

    def to_state(self) -> Dict[str, Any]:
        return {
            "deliverable": self.deliverable,
            "audience": self.audience,
            "depth": self.depth,
            "desired_length_pages": self.desired_length_pages,
            "min_citations": self.min_citations,
            "require_human_feedback": self.require_human_feedback,
            "output_path": str(self.output_path) if self.output_path else None,
            "llm": self.llm.to_state(),
            "retrieval": self.retrieval.to_state(),
            "custom_directives": list(self.custom_directives),
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "DeepResearchConfig":
        base = cls()
        llm_state = state.get("llm", {})
        retrieval_state = dict(state.get("retrieval", {}))
        retrieval_state.pop("allow_stub_fallback", None)
        return cls(
            deliverable=state.get("deliverable", base.deliverable),
            audience=state.get("audience", base.audience),
            depth=state.get("depth", base.depth),
            desired_length_pages=state.get("desired_length_pages", base.desired_length_pages),
            min_citations=state.get("min_citations", base.min_citations),
            require_human_feedback=state.get("require_human_feedback", base.require_human_feedback),
            output_path=Path(state["output_path"]) if state.get("output_path") else None,
            llm=LLMConfig(**llm_state) if llm_state else LLMConfig(),
            retrieval=RetrievalConfig(**retrieval_state) if retrieval_state else RetrievalConfig(),
            custom_directives=state.get("custom_directives", list(base.custom_directives)),
        )
