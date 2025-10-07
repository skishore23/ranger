"""In-memory registry for topology regions."""

from __future__ import annotations

from typing import Dict, List, Optional

from .types import Region

_REGISTRY: Dict[str, Region] = {}


def register_region(region: Region) -> None:
    """Register or replace a region by key."""
    _REGISTRY[region.key] = region


def get_region(key: str) -> Optional[Region]:
    """Return a registered region if present."""
    return _REGISTRY.get(key)


def list_regions(kind: Optional[str] = None) -> List[Region]:
    """Return all regions, optionally filtered by kind."""
    if kind is None:
        return list(_REGISTRY.values())
    return [region for region in _REGISTRY.values() if region.kind == kind]


def clear_registry() -> None:
    """Remove all registered regions."""
    _REGISTRY.clear()


def has_region(key: str) -> bool:
    """Check for existence of a region key."""
    return key in _REGISTRY


__all__ = [
    "register_region",
    "get_region",
    "list_regions",
    "clear_registry",
    "has_region",
]
