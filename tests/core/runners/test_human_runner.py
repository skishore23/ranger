from unittest.mock import Mock

from core.runners.human_runner import HumanRunner


def test_human_runner_run_returns_empty_dict(capsys):
    runner = HumanRunner(title="Review", description="Check output", fields=[{"name": "decision"}])
    capability = Mock()
    capability.id = "cap"
    result = runner.run(capability, Mock())
    captured = capsys.readouterr()

    assert result == {}
    assert "Human review requested: Review" in captured.out
    assert "Description: Check output" in captured.out


def test_human_runner_fields_default():
    runner = HumanRunner(title="Only title")
    assert runner.fields == []
