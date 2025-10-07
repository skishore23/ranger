"""Deep research agent package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["DeepResearchAgent"]


def __getattr__(name: str) -> Any:  # pragma: no cover - delegation helper
    if name == "DeepResearchAgent":
        return import_module("agents.deep_research.agent").DeepResearchAgent
    raise AttributeError(name)
