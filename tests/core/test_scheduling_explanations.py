"""Explanations must describe actual selection without rerunning user code."""
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from core.errors import GoalBlocked
from core.sdk import Agent, goal, step
from core.trace import replay_run
from ranger.cli import app
from topology.types import Budget


def schedules(db, run_id=None):
    return [e['payload'] for e in replay_run(db, run_id)['events'] if e['kind'] == 'schedule']


def by_id(report):
    return {row['capability']: row for row in report['capabilities']}


def test_explains_missing_unchanged_and_conflicting_work(tmp_path):
    @step(inputs=['x'], outputs=['y'], name='a.produce')
    def produce(s):
        return {'y': s['x'] + 1}

    @step(inputs=['y'], outputs=['z'], name='b.consume')
    def consume(s):
        return {'z': s['y'] * 2}

    @step(inputs=['missing'], outputs=['other'], name='c.wait')
    def waiting(s):
        pytest.fail('missing inputs must never execute')

    db = tmp_path / 'runs.db'
    result = Agent([waiting, consume, produce], trace_path=db).run(
        initial={'x': 1, 'y': 0, 'secret': 'not-a-diagnostic-value'}, goal=lambda s: s.get('z') == 4)
    assert result.ok
    reports = schedules(db)
    assert len(reports) == 2
    first, second = map(by_id, reports)
    assert first['a.produce']['status'] == 'selected'
    assert first['a.produce']['reason'] == 'never_run'
    assert first['b.consume']['status'] == 'deferred'
    assert first['b.consume']['conflicts'] == [{'capability': 'a.produce', 'keys': ['y']}]
    assert first['c.wait']['missing'] == ['missing']
    assert second['a.produce']['reason'] == 'inputs_unchanged'
    assert reports[1]['selected'] == ['b.consume']
    assert 'not-a-diagnostic-value' not in json.dumps(reports)


def test_explains_changed_inputs_and_pending_retry(tmp_path):
    attempts = []

    @step(inputs=['n'], outputs=['n'], name='a.advance')
    def advance(s):
        return {'n': s['n'] + 1} if s['n'] < 2 else {}

    @step(inputs=['n'], outputs=['done'], name='b.retry')
    def retry(s):
        attempts.append(s['n'])
        if len(attempts) == 1:
            raise GoalBlocked('llm_invalid_output')
        return {'done': True}

    db = tmp_path / 'runs.db'
    result = Agent([advance, retry], trace_path=db).run(
        initial={'n': 0}, goal=lambda s: s.exists('done'))
    assert result.ok and len(attempts) == 2
    rows = [by_id(r) for r in schedules(db)]
    assert any(r['a.advance']['reason'] == 'inputs_changed' for r in rows)
    assert any(r['b.retry']['reason'] == 'retry_pending' and r['b.retry']['attempts'] == 1 for r in rows)


@pytest.mark.parametrize('limit', ['calls', 'steps'])
def test_explains_ready_work_stopped_by_limits(tmp_path, limit):
    @step(outputs=['done'], name='ready')
    def write(s):
        pytest.fail('explanation must not execute blocked work')

    db = tmp_path / 'runs.db'
    result = Agent([write], budget=Budget(1000, 1000, 0 if limit == 'calls' else 10),
                   trace_path=db).run(initial={}, goal=lambda s: s.exists('done'),
                                      max_steps=0 if limit == 'steps' else 10)
    assert not result.ok
    report, = schedules(db)
    assert by_id(report)['ready']['status'] == 'blocked'
    assert report['stop']['reason'] == ('budget' if limit == 'calls' else 'step_limit')
    if limit == 'calls':
        assert report['stop']['details']['dimension'] == 'calls'


def test_explains_exhausted_retries(tmp_path):
    @step(outputs=['done'], name='retry')
    def retry(s):
        raise GoalBlocked('llm_unavailable')

    db = tmp_path / 'runs.db'
    result = Agent([retry], trace_path=db).run(initial={}, goal=lambda s: s.exists('done'))
    assert not result.ok and result.blocker.reason == 'llm_blocked'
    final = schedules(db)[-1]
    assert final['stop']['reason'] == 'retries_exhausted'
    assert by_id(final)['retry']['attempts'] == 3
    assert by_id(final)['retry']['status'] == 'blocked'


def test_named_goal_requirements_are_recorded_and_respected(tmp_path):
    from core.errors import GoalCheck

    @step(outputs=['count'])
    def count(s):
        return {'count': 1}

    @goal(scope=['count'])
    def done(s):
        return GoalCheck(met=s['count'] >= 2, unmet=['need two records'] if s['count'] < 2 else [])

    db = tmp_path / 'runs.db'
    result = Agent([count], trace_path=db).run(initial={}, goal=done)
    assert not result.ok
    goal_events = [e['payload'] for e in replay_run(db)['events'] if e['kind'] == 'goal']
    assert goal_events[0]['missing'] == ['count']
    assert goal_events[-1]['unmet'] == ['need two records']
    assert result.blocker.details['goal']['unmet'] == ['need two records']


def test_explain_cli_selects_run_and_never_executes_callbacks(tmp_path):
    calls = []

    @step(inputs=['x'], outputs=['done'], name='write')
    def write(s):
        calls.append(s['x'])
        return {'done': s['x']}

    db = tmp_path / 'runs.db'
    agent = Agent([write], trace_path=db)
    first = agent.run(initial={}, goal=lambda s: s.exists('done'))
    second = agent.run(initial={'x': 7}, goal=lambda s: s.exists('done'))
    before = db.read_bytes()
    cli = CliRunner()
    explained = cli.invoke(app, ['explain', str(db), '--run-id', first.run_id, '--json'])
    assert explained.exit_code == 0, explained.output
    data = json.loads(explained.output)
    assert data['run_id'] == first.run_id and data['status'] == 'blocked'
    assert data['schedules'][0]['capabilities'][0]['missing'] == ['x']
    latest = cli.invoke(app, ['explain', str(db)])
    assert latest.exit_code == 0 and second.run_id in latest.output
    assert 'selected' in latest.output and 'write' in latest.output
    assert calls == [7] and db.read_bytes() == before


def test_legacy_runs_remain_replayable_and_explain_reports_absence(tmp_path):
    from core.trace import Recorder

    db = tmp_path / 'runs.db'
    recorder = Recorder(db, {})
    recorder.append('goal', {'status': 'met', 'step': 0, 'name': 'legacy'})
    recorder.append('outcome', {'status': 'succeeded', 'state': {}, 'steps': 0, 'blocker': None})
    recorder.close()
    assert replay_run(db)['status'] == 'succeeded'
    result = CliRunner().invoke(app, ['explain', str(db), '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['schedules'] == []


@pytest.mark.parametrize('corruption', ['selected', 'reason', 'stop', 'attempts', 'ghost'])
def test_malformed_scheduling_evidence_is_rejected(tmp_path, corruption):
    @step(outputs=['done'], name='write')
    def write(s):
        return {'done': True}

    db = tmp_path / 'runs.db'
    Agent([write], trace_path=db).run(initial={}, goal=lambda s: s.exists('done'))
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT seq,payload FROM execution_events WHERE kind='schedule'").fetchone()
        assert row is not None
        payload = json.loads(row[1])
        if corruption == 'selected':
            payload['selected'] = ['invented']
        elif corruption == 'reason':
            payload['capabilities'][0]['reason'] = []
        elif corruption == 'stop':
            payload['stop'] = {'reason': [], 'details': {}}
        elif corruption == 'ghost':
            payload['selected'].append('ghost')
            payload['capabilities'].append({**payload['capabilities'][0], 'capability': 'ghost'})
        else:
            payload['capabilities'][0]['attempts'] = True
        connection.execute('UPDATE execution_events SET payload=? WHERE seq=?', (json.dumps(payload), row[0]))
    with pytest.raises(ValueError, match='[Ss]chedul'):
        replay_run(db)
