import pytest
from unittest.mock import Mock

from core.runners.python_runner import PythonRunner
from core.workspace import Snapshot
from core.capability import Capability


def test_python_runner_returns_result_dict():
    def fn(snapshot: Snapshot):
        return {"field": 42}

    runner = PythonRunner(fn)
    cap = Mock(spec=Capability)
    cap.writes = {"field"}

    result = runner.run(cap, Mock(spec=Snapshot))

    assert result == {"field": 42}


def test_python_runner_handles_none():
    runner = PythonRunner(lambda snapshot: None)
    cap = Mock(spec=Capability)
    cap.writes = set()

    result = runner.run(cap, Mock(spec=Snapshot))

    assert result == {}


def test_python_runner_rejects_non_dict():
    runner = PythonRunner(lambda snapshot: 123)
    cap = Mock(spec=Capability)
    cap.writes = set()

    with pytest.raises(TypeError):
        runner.run(cap, Mock(spec=Snapshot))


def test_python_runner_rejects_illegal_writes():
    runner = PythonRunner(lambda snapshot: {"other": 1})
    cap = Mock(spec=Capability)
    cap.id = "capability"
    cap.writes = {"allowed"}

    with pytest.raises(RuntimeError):
        runner.run(cap, Mock(spec=Snapshot))
