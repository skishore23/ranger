"""Ranger's state-driven workflow runtime.

The SDK declares capabilities; the engine schedules and executes them. Workspace
admission owns field validation, transition checks and atomic publication. State
values and strict JSON encoding are independent of execution and persistence.
Recorded evidence is reconstructed by data-only replay and query modules.

See docs/ARCHITECTURE.md for dependency boundaries and extension guidance.
"""

__version__ = "0.1.0"
