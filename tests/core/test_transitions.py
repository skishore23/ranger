"""State transitions must be checked atomically and remain inspectable as data."""
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from core.engine import Engine
from core.errors import GoalCheck
from core.merge import MergeMode, WriteSpec
from core.plan import action, plan
from core.sdk import Agent, step
from core.trace import replay_run
from core.workspace import Workspace
from ranger.cli import app


def api():
    try:
        from core.sdk import TransitionRejected
        from core.transitions import field_history, trajectory_run
    except ImportError:
        pytest.fail('Versioned transitions and transition checks are not implemented')
    return TransitionRejected, field_history, trajectory_run


def only_verified_approval(before, proposed):
    return GoalCheck(not proposed.get('approved') or proposed.get('verified') is True,
                     () if not proposed.get('approved') or proposed.get('verified') is True else ('verification required',))


def test_check_sees_latest_previous_and_complete_merged_proposal():
    api()
    seen = []
    def inspect(before, proposed):
        seen.append((before.data, proposed.data))
        assert before['record'] == {'a': 1}
        assert proposed['record'] == {'a': 1, 'b': 2}
        assert proposed['untouched'] == 'present'
        assert proposed['approved'] is True and proposed['verified'] is True
        proposed.data['record'].value['a'] = 999
        return True
    ws = Workspace({'record': {'a': 1}, 'untouched': 'present'}, transition_checks={'inspect': inspect})
    ws.cas_commit({'record': {'b': 2}, 'approved': True, 'verified': True}, provenance_id='test', writer='review',
                  write_specs={'record': WriteSpec(merge_mode=MergeMode.MERGE_JSON), 'approved': WriteSpec(), 'verified': WriteSpec()})
    assert len(seen) == 1 and ws.snapshot()['record'] == {'a': 1, 'b': 2}


@pytest.mark.parametrize('result', [False, None, 'approved', 1])
def test_checks_fail_closed_without_partial_publication(result):
    Rejected, _, _ = api()
    ws = Workspace({'count': 1}, transition_checks={'policy': lambda old, new: result})
    previous = ws.snapshot()
    with pytest.raises(Rejected) as failure:
        ws.cas_commit({'count': 2, 'approved': True}, provenance_id='test')
    assert failure.value.check == 'policy'
    assert ws.snapshot().digest() == previous.digest()


def test_check_exception_and_named_unmet_requirements():
    Rejected, _, _ = api()
    def broken(old, new):
        raise ValueError('private raw content')
    for check in (broken, only_verified_approval):
        ws = Workspace(transition_checks={'approval': check})
        with pytest.raises(Rejected) as failure:
            ws.cas_commit({'approved': True}, provenance_id='test')
        assert failure.value.unmet
        assert 'private raw content' not in str(failure.value)
        assert not ws.snapshot().exists('approved')


def test_opt_in_checks_are_captured_and_can_be_omitted():
    Rejected, _, _ = api()
    checks = {'policy': lambda old, new: False}
    ws = Workspace(transition_checks=checks)
    checks.clear()
    with pytest.raises(Rejected):
        ws.cas_commit({'x': 1}, provenance_id='test')
    Workspace().cas_commit({'x': 1}, provenance_id='legacy')


def test_checks_run_after_field_validation_and_before_deadline_gate():
    api()
    from core.sdk import FieldContract, FieldContractViolation
    calls = []
    ws = Workspace(field_contracts={'x': FieldContract({'type': 'integer'}, ['writer'])},
                   transition_checks={'policy': lambda old, new: calls.append('policy') or True})
    with pytest.raises(FieldContractViolation):
        ws.cas_commit({'x': 'bad'}, writer='writer', provenance_id='test')
    assert not calls
    ws.cas_commit({'x': 1}, writer='writer', provenance_id='test', before_publish=lambda: calls.append('gate'))
    assert calls == ['policy', 'gate']


def test_initial_state_and_successful_empty_writes_are_baselines_not_transitions():
    api()
    ws = Workspace({'x': 1}, transition_checks={'policy': lambda old, new: pytest.fail('no update to check')})
    assert ws.cas_commit({}, provenance_id='empty')['x'] == 1


@pytest.mark.parametrize('factory', [Agent, Engine])
def test_engine_rejects_update_and_records_checks_without_new_versions(tmp_path, factory):
    Rejected, history, trajectory = api()
    @step(outputs=['approved'], name='approve')
    def approve(state):
        return {'approved': True}
    db = tmp_path / 'runs.db'
    engine = factory([approve], trace=True, trace_path=db, transition_checks={'approval': only_verified_approval})
    run = engine.run if factory is Agent else engine.solve
    with pytest.raises(Rejected):
        run(initial={'approved': False}, goal=lambda s: s['approved'])
    recorded = replay_run(db)
    assert recorded['state'] == {'approved': False}
    record = trajectory(db)['transitions'][0]
    assert record['status'] == 'rejected' and record['changes'] == {}
    assert record['checks'] == [{'name': 'approval', 'met': False, 'unmet': ['verification required']}]
    assert [entry['version'] for entry in history(db, 'approved')['entries']] == [1]
    assert len(history(db, 'approved')['rejections']) == 1


def workflow(db):
    @step(inputs=['source'], outputs=['draft'], name='draft')
    def draft(state):
        return {'draft': state['source']}
    @step(inputs=['draft'], outputs=['approved'], name='review')
    def review(state):
        return {'approved': True}
    return Agent([draft, review], trace_path=db, transition_checks={'approval': lambda old, new: True}).run(
        initial={'source': 'text', 'draft': 'old'}, goal=lambda s: s.get('approved') is True)


def test_versioned_records_and_field_history_are_defensive_read_only(tmp_path):
    _, history, trajectory = api()
    db = tmp_path / 'runs.db'
    result = workflow(db)
    before = db.read_bytes()
    report = trajectory(db, result.run_id)
    records = report['transitions']
    assert report['record_version'] == 2
    assert [record['schema'] for record in records] == ['ranger.transition@1'] * 2
    assert [record['status'] for record in records] == ['committed', 'committed']
    assert records[0]['changes']['draft']['before']['version'] == 1
    assert records[0]['changes']['draft']['after']['version'] == 2
    assert records[1]['inputs']['draft']['version'] == 2
    assert records[1]['inputs']['draft']['provenance_id'] == records[0]['changes']['draft']['after']['provenance_id']
    entries = history(db, 'draft', result.run_id)['entries']
    assert [(entry['version'], entry['value']) for entry in entries] == [(1, 'old'), (2, 'text')]
    entries[1]['value'] = 'modified export'
    assert history(db, 'draft', result.run_id)['entries'][1]['value'] == 'text'
    assert history(db, 'absent', result.run_id)['entries'] == []
    assert db.read_bytes() == before


def test_identical_rewrite_is_distinct_from_successful_noop(tmp_path):
    _, history, trajectory = api()
    @step(outputs=['x'], name='a.rewrite')
    def rewrite(state):
        return {'x': 1}
    @step(name='b.noop')
    def noop(state):
        return {}
    db = tmp_path / 'runs.db'
    Agent([rewrite, noop], trace_path=db).run(initial={'x': 1}, goal=lambda s: False)
    records = trajectory(db)['transitions']
    assert [r['status'] for r in records] == ['committed', 'unchanged']
    assert records[0]['changes']['x']['changed'] is False
    assert records[1]['changes'] == {}
    assert [e['version'] for e in history(db, 'x')['entries']] == [1, 2]


def test_compatible_batch_reads_preserve_input_versions_and_checks_see_latest_commit(tmp_path):
    _, _, trajectory = api()
    seen = []
    @step(inputs=['source'], outputs=['left'], name='a.left')
    def left(state):
        return {'left': state['source']}
    @step(inputs=['source'], outputs=['right'], name='b.right')
    def right(state):
        return {'right': state['source']}
    def check(old, new):
        seen.append((old.exists('left'), new.exists('left')))
        return True
    db = tmp_path / 'runs.db'
    result = Agent([left, right], trace_path=db, transition_checks={'check': check}).run(
        initial={'source': 1}, goal=lambda s: s.exists('right'))
    assert result.ok and seen == [(False, True), (True, True)]
    assert [r['inputs']['source']['version'] for r in trajectory(db)['transitions']] == [1, 1]


def test_guarded_input_fingerprints_distinguish_selected_from_source(tmp_path, monkeypatch):
    _, _, trajectory = api()
    from core.sdk import tool
    @tool(inputs=['source'], outputs=['out'], name='guarded')
    def guarded(state):
        return {'out': state['source']}
    monkeypatch.setattr('core.engine.guard_value', lambda values: {**values, 'source': 'redacted'})
    db = tmp_path / 'runs.db'
    Agent([guarded], trace_path=db).run(initial={'source': 'original'}, goal=lambda s: s.exists('out'))
    ref = trajectory(db)['transitions'][0]['inputs']['source']
    assert ref['sha256'] != ref['source_sha256'] and ref['version'] == 1


@pytest.mark.parametrize('damage', ['version', 'before', 'inputs', 'status', 'checks', 'missing'])
def test_replay_and_queries_reject_corrupt_native_transition_records(tmp_path, damage):
    _, history, trajectory = api()
    db = tmp_path / 'runs.db'
    result = workflow(db)
    with sqlite3.connect(db) as connection:
        seq, raw = connection.execute("SELECT seq,payload FROM execution_events WHERE run_id=? AND kind='commit' ORDER BY seq LIMIT 1", (result.run_id,)).fetchone()
        payload = json.loads(raw)
        record = payload['transition']
        if damage == 'version':
            record['changes']['draft']['after']['version'] = 999
        elif damage == 'before':
            record['changes']['draft']['before']['value'] = 'forged'
        elif damage == 'inputs':
            record['inputs']['source']['version'] = 999
        elif damage == 'status':
            record['status'] = 'rejected'
        elif damage == 'checks':
            record['checks'][0]['met'] = False
        else:
            payload.pop('transition')
        connection.execute('UPDATE execution_events SET payload=? WHERE run_id=? AND seq=?', (json.dumps(payload), result.run_id, seq))
    for query in (lambda: replay_run(db), lambda: trajectory(db), lambda: history(db, 'draft')):
        with pytest.raises(ValueError):
            query()


def test_v1_recordings_remain_readable_with_explicit_legacy_history(tmp_path):
    _, history, trajectory = api()
    from core.trace import Recorder
    db = tmp_path / 'old.db'
    recorder = Recorder(db, {'x': 1})
    recorder.append('goal', {'status': 'met', 'step': 0, 'name': 'done'})
    recorder.append('outcome', {'status': 'succeeded', 'state': {'x': 1}, 'steps': 0, 'blocker': None})
    recorder.close()
    assert replay_run(db)['state'] == {'x': 1}
    assert trajectory(db)['record_version'] == 1
    assert history(db, 'x')['entries'][0]['legacy'] is True


def test_cli_history_and_trajectory_and_run_selection(tmp_path):
    _, history, _ = api()
    db = tmp_path / 'runs.db'
    first = workflow(db)
    Agent([], trace_path=db).run(initial={'draft': 'new run'}, goal=lambda s: True)
    runner = CliRunner()
    for command in ('trajectory', 'history'):
        args = [command, str(db)] + (['draft'] if command == 'history' else []) + ['--run-id', first.run_id, '--json']
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)['run_id'] == first.run_id
    assert history(db, 'draft')['entries'][0]['value'] == 'new run'


def test_named_checks_preserve_registration_order(tmp_path):
    _, _, trajectory = api()
    @step(outputs=['x'], name='write')
    def write(state):
        return {'x': 1}
    db = tmp_path / 'runs.db'
    Agent([write], trace_path=db, transition_checks={
        'z.first': lambda old, new: True, 'a.second': lambda old, new: True,
    }).run(initial={}, goal=lambda s: s.exists('x'))
    assert [check['name'] for check in trajectory(db)['transitions'][0]['checks']] == ['z.first', 'a.second']


def test_legacy_queries_do_not_trust_unvalidated_native_metadata(tmp_path):
    _, history, trajectory = api()
    from core.trace import Recorder
    db = tmp_path / 'old.db'
    recorder = Recorder(db, {'x': 1})
    recorder.append('goal', {'status': 'unmet', 'step': 0, 'name': 'done'})
    recorder.append('capability_started', {'capability': 'legacy'})
    recorder.append('commit', {'capability': 'legacy', 'writes': {'x': 2}, 'state': {'x': 2},
                               'transition': {'schema': 'unvalidated-extra'}})
    recorder.append('capability_finished', {'capability': 'legacy', 'status': 'succeeded', 'duration_ms': 0})
    recorder.append('goal', {'status': 'met', 'step': 1, 'name': 'done'})
    recorder.append('outcome', {'status': 'succeeded', 'state': {'x': 2}, 'steps': 1, 'blocker': None})
    recorder.close()
    assert trajectory(db)['transitions'] == []
    assert all(entry['legacy'] for entry in history(db, 'x')['entries'])


@pytest.mark.parametrize('kind', ['initial', 'goal', 'schedule', 'capability_started', 'outcome'])
def test_native_transition_cannot_be_attached_to_unrelated_event(tmp_path, kind):
    _, history, trajectory = api()
    db = tmp_path / 'runs.db'
    result = workflow(db)
    with sqlite3.connect(db) as connection:
        seq, raw = connection.execute('SELECT seq,payload FROM execution_events WHERE run_id=? AND kind=? ORDER BY seq LIMIT 1',
                                       (result.run_id, kind)).fetchone()
        payload = json.loads(raw)
        payload['transition'] = {'schema': 'ranger.transition@1', 'status': 'rejected',
                                 'declared_writes': ['draft'], 'proposed_keys': ['draft']}
        connection.execute('UPDATE execution_events SET payload=? WHERE run_id=? AND seq=?',
                            (json.dumps(payload), result.run_id, seq))
    for query in (lambda: replay_run(db), lambda: trajectory(db), lambda: history(db, 'draft')):
        with pytest.raises(ValueError):
            query()


def test_transition_check_failure_keeps_earlier_compatible_commits(tmp_path):
    Rejected, history, _ = api()
    @step(outputs=['left'], name='a.left')
    def left(state):
        return {'left': 1}
    @step(outputs=['approved'], name='b.approve')
    def approve(state):
        return {'approved': True}
    db = tmp_path / 'runs.db'
    with pytest.raises(Rejected):
        Agent([left, approve], trace_path=db, transition_checks={'approval': only_verified_approval}).run(
            initial={}, goal=lambda s: s.get('approved'))
    assert replay_run(db)['state'] == {'left': 1}
    assert len(history(db, 'left')['entries']) == 1


@pytest.mark.parametrize('damage', ['missing_checks', 'passed_check', 'other_error'])
def test_replay_rejects_contradictory_transition_check_failure(tmp_path, damage):
    Rejected, history, trajectory = api()
    @step(outputs=['x'], name='write')
    def write(state):
        return {'x': 1}
    db = tmp_path / 'runs.db'
    with pytest.raises(Rejected):
        Agent([write], trace_path=db, transition_checks={'deny': lambda old, new: False}).run(
            initial={}, goal=lambda s: s.exists('x'))
    with sqlite3.connect(db) as connection:
        seq, raw = connection.execute("SELECT seq,payload FROM execution_events WHERE kind='capability_finished'").fetchone()
        payload = json.loads(raw)
        record = payload['transition']
        if damage == 'missing_checks':
            record['checks'] = []
        elif damage == 'passed_check':
            record['checks'][0].update(met=True, unmet=[])
        else:
            record['error']['type'] = 'RuntimeError'
        connection.execute('UPDATE execution_events SET payload=? WHERE seq=?', (json.dumps(payload), seq))
    for query in (lambda: replay_run(db), lambda: trajectory(db), lambda: history(db, 'x')):
        with pytest.raises(ValueError):
            query()


@pytest.mark.parametrize('site', ['runner', 'validator', 'postcondition'])
def test_user_raised_transition_error_still_has_replayable_execution_failure(tmp_path, site):
    Rejected, _, trajectory = api()
    def reject(*args):
        raise Rejected('custom', ['not allowed'], [])
    @step(outputs=['x'], name='write')
    def write(state):
        if site == 'runner':
            reject()
        return {'x': 1}
    from dataclasses import replace
    if site == 'validator':
        write = replace(write, write_specs={'x': WriteSpec(validator=reject)})
    elif site == 'postcondition':
        write = replace(write, post=reject)
    db = tmp_path / 'runs.db'
    with pytest.raises(Rejected):
        Agent([write], trace_path=db).run(initial={}, goal=lambda s: s.exists('x'))
    assert replay_run(db)['state'] == {}
    record = trajectory(db)['transitions'][0]
    assert record['checks'] == [] and record['error']['source'] == 'execution'


def test_deadline_gate_after_policy_rejects_publication():
    api()
    ws = Workspace({'x': 1}, transition_checks={'policy': lambda old, new: True})
    def expired():
        raise RuntimeError('deadline')
    with pytest.raises(RuntimeError, match='deadline'):
        ws.cas_commit({'x': 2}, provenance_id='test', before_publish=expired)
    assert ws.snapshot()['x'] == 1 and ws.snapshot().data['x'].version == 1


def test_plan_and_runtime_forward_transition_checks(tmp_path):
    Rejected, _, _ = api()
    from agents.common.runtime import AgentRuntime
    from topology.types import Budget
    @step(outputs=['x'], name='write')
    def write(state):
        return {'x': 1}
    checks = {'deny': lambda old, new: False}
    compiled = plan(action(write)).compile(transition_checks=checks)
    with pytest.raises(Rejected):
        compiled.run(initial={}, goal=lambda s: s.exists('x'))
    runtime = AgentRuntime(repo_root=tmp_path, capability_list=[write], budget=Budget(10000, 10000, 10),
                           memory_key='transitions', memory_domain='transitions', db_filename='run.db', transition_checks=checks)
    with pytest.raises(Rejected):
        runtime.run_agent(initial={}, goal=lambda s: s.exists('x'), max_steps=5)
