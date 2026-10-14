"""Fresh approval checks must override cached terminal/progress flags."""
import subprocess
from pathlib import PureWindowsPath

import pytest

from agents.testwriter import capabilities as tw
from core.sdk import Agent
from core.workspace import Workspace
from tests.test_reference_agent_reliability import writer, SuiteProvider


@pytest.mark.parametrize('change', ['source', 'tests', 'settings', 'missing', 'attempt', 'inventory', 'module', 'coverage'])
def test_cached_success_cannot_approve_changed_subject(tmp_path, change):
    agent = writer(tmp_path, SuiteProvider())
    result = agent.run()
    assert result.ok, result.blocker
    state = {key: entry.value for key, entry in result.final.data.items()}
    assert tw.tests_finished(Workspace(state).snapshot())
    if change == 'source':
        (tmp_path / 'pkg/mod1.py').write_text('def value():\n    return 2\n')
    elif change == 'tests':
        (tmp_path / 'tests/test_mod1.py').write_text('def test_unrelated():\n    assert True\n')
    elif change == 'settings':
        state['testwriter.config']['pytest_args'] = ['-k', 'nothing_matches']
    elif change == 'missing':
        state['tests.progress']['pkg/mod1.py'].pop('pytest_evidence', None)
    elif change == 'attempt':
        state['tests.active.entry'] = {'path': 'pkg/mod1.py', 'module': 'pkg.mod1', 'attempt': 2}
    elif change == 'module':
        state['tests.active.entry'] = {'path': 'pkg/mod1.py', 'module': 'pkg.other', 'attempt': 1}
    elif change == 'coverage':
        state['tests.progress']['pkg/mod1.py'].pop('coverage_evidence', None)
    else:
        (tmp_path / 'tests/other.py').write_text('def test_other():\n    assert True\n')
        state['tests.progress']['pkg/mod1.py']['test_paths'].append('tests/other.py')
    assert not tw.tests_finished(Workspace(state).snapshot())


@pytest.mark.parametrize('change', ['plan_module', 'content', 'identity', 'inventory', 'dangling', 'unknown_target'])
def test_pending_candidate_cannot_inherit_previous_approval(tmp_path, change):
    result = writer(tmp_path, SuiteProvider()).run()
    assert result.ok, result.blocker
    state = {key: entry.value for key, entry in result.final.data.items()}
    entry = {**state['tests.todo'][0], 'attempt': 1}
    state['tests.active.entry'] = entry
    state['tests.write.requests'] = [{
        'identity': tw._identity(entry), 'path': 'tests/test_mod1.py',
        'content': (tmp_path / 'tests/test_mod1.py').read_bytes().decode('utf-8'),
    }]
    assert Agent([], trace=False).run(initial=state, goal=tw.tests_finished).ok
    if change == 'plan_module':
        state['tests.todo'][0]['module'] = 'pkg.other'
    elif change == 'content':
        state['tests.write.requests'][0]['content'] = 'def test_replacement():\n    assert False\n'
    elif change == 'identity':
        state['tests.write.requests'][0]['identity']['attempt'] = 2
    elif change == 'inventory':
        state['tests.write.requests'].append({
            'identity': tw._identity(entry), 'path': 'tests/test_extra.py',
            'content': 'def test_extra():\n    assert False\n',
        })
    elif change == 'dangling':
        state['tests.active.entry'] = None
    else:
        state['tests.active.entry'] = {'path': 'pkg/unknown.py', 'module': 'pkg.unknown', 'attempt': 1}
        state['tests.write.requests'] = []
    stale = Agent([], trace=False).run(initial=state, goal=tw.tests_finished)
    assert not stale.ok
    assert stale.blocker.details['goal']['unmet']


def test_requested_artifact_manifest_uses_portable_paths(monkeypatch):
    manifest = getattr(tw, '_requested_artifacts', None)
    assert manifest is not None, 'Canonical request manifest helper is not implemented'
    root = PureWindowsPath('C:/repo')
    monkeypatch.setattr(tw, '_safe_test_path', lambda root, path: root / path)
    artifacts = manifest(root, [{'path': 'tests/test_module.py', 'content': 'assert True\n'}])
    assert artifacts[0]['path'] == 'tests/test_module.py'


def test_malformed_pending_requests_fail_closed(tmp_path):
    result = writer(tmp_path, SuiteProvider()).run()
    assert result.ok, result.blocker
    state = {key: entry.value for key, entry in result.final.data.items()}
    state.pop('tests.write.requests')
    assert Agent([], trace=False).run(initial=state, goal=tw.tests_finished).ok
    for valid in (None, []):
        state['tests.write.requests'] = valid
        assert Agent([], trace=False).run(initial=state, goal=tw.tests_finished).ok
    for invalid in ({}, '', 0, False):
        state['tests.write.requests'] = invalid
        stale = Agent([], trace=False).run(initial=state, goal=tw.tests_finished)
        assert not stale.ok, f'Invalid request value was approved: {invalid!r}'
        assert stale.blocker.details['goal']['unmet']


def test_malformed_pending_content_is_an_unmet_requirement(tmp_path):
    result = writer(tmp_path, SuiteProvider()).run()
    assert result.ok, result.blocker
    state = {key: entry.value for key, entry in result.final.data.items()}
    entry = {**state['tests.todo'][0], 'attempt': 1}
    state['tests.active.entry'] = entry
    state['tests.write.requests'] = [{
        'identity': tw._identity(entry), 'path': 'tests/test_mod1.py', 'content': None,
    }]
    invalid = Agent([], trace=False).run(initial=state, goal=tw.tests_finished)
    assert not invalid.ok
    assert invalid.blocker.details['goal']['unmet']


def test_source_change_during_validation_cannot_publish_passing_evidence(tmp_path, monkeypatch):
    agent = writer(tmp_path, SuiteProvider())
    execute = subprocess.run

    def run_and_change_source(*args, **kwargs):
        result = execute(*args, **kwargs)
        (tmp_path / 'pkg/mod1.py').write_text('def value():\n    return 2\n')
        return result

    monkeypatch.setattr(tw.subprocess, 'run', run_and_change_source)
    result = agent.run()
    assert not result.ok
    assert result.blocker.reason == 'validation_inputs_changed'
    assert result.final.get('tests.run.result') is None
