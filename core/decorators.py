from __future__ import annotations
from typing import Callable, Any, Dict, Optional, List
from .capability import Capability
from .runners.python_runner import PythonRunner
from .merge import WriteSpec


def capability(*, reads: List[str] = (), writes: List[str] = ()):  # code-first, no DSL
    def dec(fn: Callable[[Any], Optional[Dict[str, Any]]]):
        default_specs = {k: WriteSpec() for k in set(writes)}
        cap = Capability(
            id=f"{fn.__module__}.{fn.__name__}",
            reads=set(reads),
            writes=set(writes),
            runner=PythonRunner(fn),
            write_specs=default_specs,
            tags=set(),
            cost_estimate={},
        )
        setattr(fn, "__ranger_cap__", cap)
        return fn
    return dec


def goal(*, scope: set[str]):
    def dec(fn: Callable[[Any], bool]):
        setattr(fn, "__ranger_goal_scope__", scope)
        return fn
    return dec
