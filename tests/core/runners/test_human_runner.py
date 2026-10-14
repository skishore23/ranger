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


def test_select_keeps_legacy_first_option_default(monkeypatch):
    import core.runners.human_runner as human_runner
    from core.workspace import Workspace
    monkeypatch.setattr(human_runner.sys.stdin, 'isatty', lambda: True)
    monkeypatch.delenv('RANGER_FIELD_DECISION', raising=False)
    monkeypatch.setattr('builtins.input', lambda *a: '')
    runner = HumanRunner(title='Optional choice', fields=[{'name': 'decision', 'type': 'select', 'options': ['approve', 'revise']}])
    assert runner.run(Mock(), Workspace().snapshot()) == {'decision': 'approve'}


def test_select_no_default_preserves_blank_for_callback(monkeypatch):
    import core.runners.human_runner as human_runner
    from core.workspace import Workspace
    monkeypatch.setattr(human_runner.sys.stdin, 'isatty', lambda: True)
    monkeypatch.delenv('RANGER_FIELD_DECISION', raising=False)
    monkeypatch.setattr('builtins.input', lambda *a: '')
    runner = HumanRunner(title='Explicit choice', fields=[{'name': 'decision', 'type': 'select', 'options': ['approve', 'revise'], 'default_first_option': False}], callback=lambda form: form)
    assert runner.run(Mock(), Workspace().snapshot()) == {'decision': ''}
