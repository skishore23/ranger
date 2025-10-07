"""Stub capabilities for CLI visualization tests."""

from __future__ import annotations

from core.sdk import goal, step
from core.workspace import Snapshot


@step(inputs=["input.a"], outputs=["mid.b"])
def first_step(ws: Snapshot):
    value = ws.get("input.a", 0)
    return {"mid.b": value + 1}


@step(inputs=["mid.b"], outputs=["output.c"])
def second_step(ws: Snapshot):
    value = ws.get("mid.b", 0)
    return {"output.c": value * 2}


@goal(scope=["output.c"])
def stub_goal(ws: Snapshot) -> bool:
    return ws.get("output.c") == 4


CAPABILITIES = [first_step, second_step]
