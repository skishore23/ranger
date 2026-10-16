"""Read recordings produced before the internal state/trace refactor."""
import json
from pathlib import Path
import sqlite3

import pytest

from core.trace import replay_run
from core.transitions import field_history, trajectory_run


@pytest.fixture
def archived_runs(tmp_path):
    archive = Path(__file__).parents[1] / 'fixtures/execution_0768f23.json'
    db = tmp_path / 'archive.db'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE execution_runs (run_id TEXT PRIMARY KEY, version INTEGER, '
                           'started_ns INTEGER, status TEXT, event_count INTEGER, domain TEXT)')
        connection.execute('CREATE TABLE execution_events (run_id TEXT, seq INTEGER, kind TEXT, '
                           'elapsed_ms REAL, payload TEXT, PRIMARY KEY (run_id, seq))')
        for fixture in json.loads(archive.read_text()):
            run = fixture['run']
            connection.execute('INSERT INTO execution_runs VALUES (?,?,?,?,?,?)',
                               [run[key] for key in ('run_id', 'version', 'started_ns', 'status', 'event_count', 'domain')])
            for event in fixture['events']:
                connection.execute('INSERT INTO execution_events VALUES (?,?,?,?,?)',
                                   [event[key] for key in ('run_id', 'seq', 'kind', 'elapsed_ms', 'payload')])
    return db


def test_native_archive_retains_atomic_failure_and_prior_versions(archived_runs):
    before = archived_runs.read_bytes()
    replay = replay_run(archived_runs, 'fixture-0')
    assert replay['status'] == 'failed'
    assert replay['state'] == {'record': {'a': 1, 'b': 2}, 'source': 'seed'}
    trajectory = trajectory_run(archived_runs, 'fixture-0')['transitions']
    assert [(row['writer'], row['status']) for row in trajectory] == [
        ('a.merge', 'committed'), ('b.noop', 'unchanged'), ('c.reject', 'rejected')]
    assert trajectory[0]['inputs']['source']['version'] == 1
    assert trajectory[-1]['checks'] == [{'name': 'approval', 'met': False, 'unmet': ['approval denied']}]
    assert trajectory[-1]['error']['source'] == 'transition_checks'
    history = field_history(archived_runs, 'record', 'fixture-0')
    assert [(entry['version'], entry['value']) for entry in history['entries']] == [
        (1, {'a': 1}), (2, {'a': 1, 'b': 2})]
    rejected = field_history(archived_runs, 'approved', 'fixture-0')
    assert rejected['entries'] == [] and len(rejected['rejections']) == 1
    assert archived_runs.read_bytes() == before


def test_native_archive_keeps_user_exception_distinct_from_policy_rejection(archived_runs):
    assert replay_run(archived_runs, 'fixture-1')['state'] == {}
    record = trajectory_run(archived_runs, 'fixture-1')['transitions'][0]
    assert record['error']['type'] == 'TransitionRejected'
    assert record['error']['source'] == 'execution'
    assert record['checks'] == []


def test_legacy_archive_remains_derived_and_selectable(archived_runs):
    run = replay_run(archived_runs)
    assert run['run_id'] == 'fixture-2' and run['record_version'] == 1
    assert run['status'] == 'succeeded' and run['state'] == {'record': {'a': 1, 'b': 2}}
    assert trajectory_run(archived_runs)['transitions'] == []
    history = field_history(archived_runs, 'record')['entries']
    assert [entry['version'] for entry in history] == [1, 2]
    assert all(entry['legacy'] and entry['inputs'] is None for entry in history)
