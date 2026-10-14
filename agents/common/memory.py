"""Bind reference-agent writers to the effective memory for one runtime call."""
from contextlib import contextmanager
from contextvars import ContextVar

from topology.registry import get_region


_BINDING: ContextVar[tuple[str, str] | None] = ContextVar('ranger_reference_memory', default=None)


@contextmanager
def memory_scope(key: str, domain: str):
    token = _BINDING.set((key, domain))
    try:
        yield
    finally:
        _BINDING.reset(token)


def writer_memory(default_key: str, default_domain: str):
    binding = _BINDING.get()
    key, domain = binding or (default_key, default_domain)
    region = get_region(key)
    if binding is not None and (region is None or getattr(region, 'domain', None) != domain):
        raise RuntimeError(f'Configured reference memory unavailable: {key} ({domain})')
    return region, domain
