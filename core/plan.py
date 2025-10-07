"""Plan builder utilities for composing capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Union

from topology.types import Budget

from .errors import GoalBlocked
from .capability import Capability
from .sdk import Agent


@dataclass(frozen=True)
class Action:
    """Thin wrapper that treats a capability as an action node."""

    capability: Capability
    requires_initial: frozenset[str] = frozenset()
    note: Optional[str] = None

    @property
    def inputs(self) -> frozenset[str]:
        return frozenset(self.capability.reads or set())

    @property
    def outputs(self) -> frozenset[str]:
        return frozenset(self.capability.writes or set())

    def then(self, other: Union["Action", "Plan", Capability]) -> "Plan":
        return Plan([self, other])

    def describe(self) -> str:
        note = f" — {self.note}" if self.note else ""
        initial = (
            f" (requires initial: {sorted(self.requires_initial)})"
            if self.requires_initial
            else ""
        )
        return f"{self.capability.id}{initial}{note}"


class Plan:
    """Ordered composition of actions with developer-friendly ergonomics."""

    def __init__(self, components: Iterable[Union[Action, "Plan", Capability]]):
        self._actions: List[Action] = []
        for component in components:
            self._extend_with(component)
        if not self._actions:
            raise ValueError("Plan requires at least one action")
        self._dangling: List[tuple[str, frozenset[str]]] = []
        self._initial_required: frozenset[str] = frozenset()
        self._analyse_inputs()

    def _extend_with(self, component: Union[Action, "Plan", Capability]) -> None:
        if isinstance(component, Plan):
            self._actions.extend(component._actions)
            return
        if isinstance(component, Action):
            self._actions.append(component)
            return
        if isinstance(component, Capability):
            self._actions.append(Action(component))
            return
        raise TypeError(f"Unsupported plan component type: {type(component)!r}")

    def _analyse_inputs(self) -> None:
        available: set[str] = set()
        dangling: List[tuple[str, frozenset[str]]] = []
        initial_required: set[str] = set()
        for action in self._actions:
            allowed_initial = set(action.requires_initial or set())
            missing = action.inputs - available - allowed_initial
            if missing:
                dangling.append((action.capability.id, frozenset(missing)))
            needed_initial = (action.inputs - available) & allowed_initial
            if needed_initial:
                initial_required |= needed_initial
            available |= set(action.outputs)
        self._dangling = dangling
        self._initial_required = frozenset(initial_required)

    def then(self, other: Union[Action, "Plan", Capability]) -> "Plan":
        return Plan([*self._actions, other])

    def __rshift__(self, other: Union[Action, "Plan", Capability]) -> "Plan":
        return self.then(other)

    @property
    def actions(self) -> Sequence[Action]:
        return tuple(self._actions)

    @property
    def capabilities(self) -> Sequence[Capability]:
        return tuple(action.capability for action in self._actions)

    @property
    def missing_inputs(self) -> Sequence[tuple[str, frozenset[str]]]:
        """Inputs not produced by earlier actions or marked as initial."""

        return tuple(self._dangling)

    @property
    def initial_requirements(self) -> frozenset[str]:
        return self._initial_required

    def validate(self, *, strict: bool = False) -> Sequence[tuple[str, frozenset[str]]]:
        """Return missing inputs; optionally raise if strict."""

        if strict and self._dangling:
            details = {
                "missing": [
                    {"action": action_id, "needs": sorted(missing)}
                    for action_id, missing in self._dangling
                ],
                "initial_requirements": sorted(self._initial_required),
            }
            raise GoalBlocked("plan_missing_inputs", details=details)
        return list(self._dangling)

    def describe(self) -> str:
        lines = ["Plan composition:"]
        for idx, action in enumerate(self._actions, start=1):
            lines.append(f"  {idx}. {action.describe()}")
        if self._initial_required:
            lines.append(
                f"  initial requirements: {sorted(self._initial_required)}"
            )
        if self._dangling:
            lines.append("  unresolved inputs:")
            for action_id, missing in self._dangling:
                lines.append(f"    - {action_id} missing {sorted(missing)}")
        return "\n".join(lines)

    def compile(self, *, budget: Budget | None = None, strict: bool = True) -> Agent:
        self.validate(strict=strict)
        return Agent(list(self.capabilities), budget=budget)


def action(
    capability: Capability,
    *,
    requires_initial: Optional[Iterable[str]] = None,
    note: Optional[str] = None,
) -> Action:
    """Explicit helper for wrapping a capability as an action."""

    required = frozenset(requires_initial or [])
    return Action(capability, requires_initial=required, note=note)


def plan(*components: Union[Action, Plan, Capability]) -> Plan:
    """Build a plan from capabilities or actions."""

    return Plan(components)


__all__ = ["Plan", "Action", "plan", "action"]
