"""Topology boot helpers for registering reusable region sets."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from topology.registry import clear_registry, has_region, register_region
from topology.types import Budget
from regions import LLMOpenAI, MemSQLite


DEFAULT_DB_PATH = Path(".ranger_topology.db")


def setup_memory(
    *,
    reset: bool = True,
    db_path: Optional[str] = None,
    memory_key: str = "mem.sqlite",
    domain: str = "default",
    guards: Optional[list] = None,
    purge: bool = False,
) -> None:
    """Install the default SQLite-backed memory and optional guardrails.

    Args:
        reset: Clear the registry before registering regions.
        db_path: Optional path for the SQLite backing store.
        memory_key: Registry key for the memory region.
        domain: Domain facet stored on emitted atoms.
        guards: Optional iterable of guard region instances to register.
    """
    if reset:
        clear_registry()

    memory_path = Path(db_path or DEFAULT_DB_PATH)
    memory_region = MemSQLite(memory_key, str(memory_path), domain)
    if purge:
        memory_region.purge()
    register_region(memory_region)

    if guards:
        for guard in guards:
            register_region(guard)


def add_guardrails(*guards) -> None:
    """Add guard regions after the memory setup has already run."""

    for guard in guards:
        register_region(guard)


def setup_openai_llm(
    *,
    key: str,
    model: str,
    temperature: float = 0.2,
    system_prompt: Optional[str] = None,
) -> None:
    """Register an OpenAI-backed model region under ``key``."""
    register_region(
        LLMOpenAI(
            key,
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
        )
    )


def ensure_memory_ready() -> None:
    """Make sure the default memory region exists."""
    if not has_region("mem.sqlite"):
        setup_memory(reset=False)


def get_default_budget() -> Budget:
    return Budget(tokens=8000, ms=60000, calls=12)


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    setup_memory()
    print("✅ Memory region ready")
    print(f"📦 Database: {DEFAULT_DB_PATH.resolve()}")
    print("🔧 Call setup_openai_llm(...) to connect an LLM")
    print("🛡️  Call add_guardrails(...) when you need guard rules")
