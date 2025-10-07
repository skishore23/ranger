from __future__ import annotations

import pytest

from core.errors import GoalBlocked
from core.plan import plan, action
from core.sdk import step


@step(inputs=["seed"], outputs=["mid"])
def produce_mid(snapshot):
    return {"mid": snapshot.get("seed") + 1}


@step(inputs=["mid"], outputs=["result"])
def finalize(snapshot):
    return {"result": snapshot.get("mid") * 2}


def test_plan_requires_initial_keys_strict():
    composed = plan(action(produce_mid), action(finalize))
    with pytest.raises(GoalBlocked) as exc:
        composed.compile()
    assert exc.value.reason == "plan_missing_inputs"


def test_plan_validate_and_compile_with_initial_marks():
    composed = plan(
        action(produce_mid, requires_initial={"seed"}),
        action(finalize),
    )
    assert composed.validate(strict=False) == []
    agent = composed.compile()
    assert agent.engine.capabilities[0].id.endswith("produce_mid")


def test_plan_describe_mentions_actions_and_missing():
    composed = plan(action(produce_mid))
    description = composed.describe()
    assert "produce_mid" in description
    assert "unresolved" in description
