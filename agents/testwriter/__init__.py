"""Autonomous test writer agent package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["TestWriterAgent"]


def __getattr__(name: str) -> Any:  # pragma: no cover - simple re-export helper
    if name == "TestWriterAgent":
        return import_module("agents.testwriter.agent").TestWriterAgent
    raise AttributeError(name)
