"""Durable execution evidence. Replay is data-only and opens SQLite read-only.

State supports only JSON null/bool/int/finite float/string/list/string-keyed dict.
Tuples, sets, non-string keys, custom objects and non-finite numbers are rejected
before publication. Disable tracing explicitly for applications requiring them.
Recording errors propagate; interrupted runs retain status ``running`` and cannot
certify success. This local log is evidence, not a tamper-proof audit service.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


_active = ContextVar('ranger_trace', default=None)


def dumps(value):
    """Encode supported values without coercion or silently losing type information."""
    ancestors = set()

    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) in (list, dict):
            if id(item) in ancestors:
                raise TypeError('Unsupported circular JSON value')
            if type(item) is dict and any(type(k) is not str for k in item):
                raise TypeError('JSON object keys must be strings')
            ancestors.add(id(item))
            for child in (item.values() if type(item) is dict else item):
                check(child)
            ancestors.remove(id(item))
            return
        raise TypeError(f'Unsupported JSON value: {type(item).__name__}; use finite JSON values or trace=False')
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def values(snapshot):
    return {key: entry.value for key, entry in snapshot.data.items()}


def emit(kind, **payload):
    recorder = _active.get()
    if recorder is not None:
        recorder.append(kind, payload)


def validate(value):
    if _active.get() is not None:
        dumps(value)


@contextmanager
def recording(recorder):
    token = _active.set(recorder)
    try:
        yield
    finally:
        _active.reset(token)


class Recorder:
    def __init__(self, db_path, initial, domain=None):
        dumps(initial)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.run_id = uuid.uuid4().hex
        self.seq = 0
        self.started = time.monotonic()
        try:
            with self.connection:
                self.connection.execute('''CREATE TABLE IF NOT EXISTS execution_runs (
                    run_id TEXT PRIMARY KEY, version INTEGER NOT NULL, started_ns INTEGER NOT NULL,
                    status TEXT NOT NULL, event_count INTEGER NOT NULL, domain TEXT)''')
                self.connection.execute('''CREATE TABLE IF NOT EXISTS execution_events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
                    elapsed_ms REAL NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq), FOREIGN KEY (run_id) REFERENCES execution_runs(run_id))''')
                if 'domain' not in {r[1] for r in self.connection.execute('PRAGMA table_info(execution_runs)')}:
                    self.connection.execute('ALTER TABLE execution_runs ADD COLUMN domain TEXT')
                self.connection.execute("INSERT INTO execution_runs (run_id,version,started_ns,status,event_count,domain) VALUES (?,1,?,'running',0,?)",
                                        (self.run_id, time.time_ns(), domain))
            self.append('initial', {'state': initial})
        except BaseException:
            self.connection.close()
            raise

    def append(self, kind, payload):
        encoded = dumps(payload)
        with self.connection:
            self.connection.execute('INSERT INTO execution_events VALUES (?,?,?,?,?)',
                                    (self.run_id, self.seq, kind, (time.monotonic() - self.started) * 1000, encoded))
            self.connection.execute('UPDATE execution_runs SET event_count=? WHERE run_id=?', (self.seq + 1, self.run_id))
            if kind == 'outcome':
                self.connection.execute('UPDATE execution_runs SET status=? WHERE run_id=?', (payload['status'], self.run_id))
        self.seq += 1

    def close(self):
        self.connection.close()


def readonly(db_path):
    return sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)


def replay_run(db_path, run_id=None) -> dict:
    """Return a selected (or latest started) run, rejecting malformed evidence."""
    conn = readonly(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    try:
        if run_id is None:
            row = conn.execute('SELECT * FROM execution_runs ORDER BY started_ns DESC, rowid DESC LIMIT 1').fetchone()
        else:
            row = conn.execute('SELECT * FROM execution_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('No matching execution run')
        events = conn.execute('SELECT * FROM execution_events WHERE run_id=? ORDER BY seq', (row['run_id'],)).fetchall()
    finally:
        conn.close()
    if row['version'] != 1 or len(events) != row['event_count'] or not events:
        raise ValueError('Malformed execution run')
    state, decoded, terminal = {}, [], None
    active_capability = None
    required_outcomes = None
    last_goal = None
    previous_elapsed = -1
    previous_kind = None
    current_schedule = None
    schedule_index = 0
    allowed = {'initial', 'goal', 'schedule', 'capability_started', 'capability_finished', 'commit', 'memory_atom', 'outcome'}
    for index, event in enumerate(events):
        try:
            payload = json.loads(event['payload'], object_pairs_hook=_unique_object)
            dumps(payload)
        except (ValueError, TypeError) as exc:
            raise ValueError('Malformed execution event JSON') from exc
        kind = event['kind']
        if (event['seq'] != index or kind not in allowed or not isinstance(payload, dict)
                or type(event['elapsed_ms']) not in (int, float) or not math.isfinite(event['elapsed_ms']) or event['elapsed_ms'] < previous_elapsed
                or terminal is not None or (index == 0) != (kind == 'initial')):
            raise ValueError('Malformed execution event sequence')
        if required_outcomes is not None and kind != 'outcome':
            raise ValueError('Execution continued after a terminal capability or goal result')
        previous_elapsed = event['elapsed_ms']
        if kind == 'capability_started':
            if active_capability is not None or not isinstance(payload.get('capability'), str):
                raise ValueError('Malformed capability start')
            active_capability = payload['capability']
            if current_schedule is not None:
                selected = current_schedule['selected']
                if schedule_index >= len(selected) or active_capability != selected[schedule_index]:
                    raise ValueError('Capability contradicts scheduling evidence')
                schedule_index += 1
        if kind in {'commit', 'capability_finished'}:
            if active_capability is None or payload.get('capability') != active_capability:
                raise ValueError('Malformed capability event order')
        if kind == 'capability_finished':
            duration = payload.get('duration_ms')
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError('Malformed capability duration')
            if payload.get('status') in {'failed', 'blocked'}:
                required_outcomes = {payload['status']}
            active_capability = None
        if kind in {'goal', 'outcome'} and active_capability is not None:
            raise ValueError('Unfinished capability evidence')
        if kind == 'goal' and previous_kind == 'goal':
            raise ValueError('Duplicate goal evidence')
        if kind == 'memory_atom':
            atom = payload.get('atom')
            if (not isinstance(payload.get('domain'), str) or not isinstance(atom, dict)
                    or not {'id', 'modality', 'schema', 'content', 'facets', 'provenance', 'policy'} <= atom.keys()
                    or not isinstance(atom['id'], str) or not isinstance(atom['modality'], str)
                    or not all(isinstance(atom[key], dict) for key in ('facets', 'provenance', 'policy'))
                    or type(payload.get('timestamp')) is not int):
                raise ValueError('Malformed memory evidence')
        if kind in {'initial', 'commit'}:
            snapshot = payload.get('state')
            if not isinstance(snapshot, dict):
                raise ValueError('Malformed state snapshot')
            if kind == 'commit':
                writes = payload.get('writes')
                if not isinstance(writes, dict) or dumps({**state, **writes}) != dumps(snapshot):
                    raise ValueError('Inconsistent committed state')
            state = snapshot
        if kind == 'goal':
            if current_schedule is not None and schedule_index != len(current_schedule['selected']):
                raise ValueError('Goal checked before scheduled work completed')
            status, step = payload.get('status'), payload.get('step')
            if status not in {'met', 'unmet', 'blocked', 'failed'} or type(step) is not int or step < 0:
                raise ValueError('Malformed goal evidence')
            if last_goal is not None and step <= last_goal['step']:
                raise ValueError('Non-increasing goal step')
            for field in ('missing', 'unmet'):
                if field in payload and (not isinstance(payload[field], list)
                        or any(not isinstance(item, str) or not item for item in payload[field])):
                    raise ValueError('Malformed goal requirements')
            if status == 'met' and (payload.get('missing') or payload.get('unmet')):
                raise ValueError('Met goal has unmet requirements')
            current_schedule = None
            schedule_index = 0
            last_goal = {'status': status, 'step': step, 'state': dumps(state)}
            if status == 'met':
                # A met goal ends solve; a late deadline can still block its outcome.
                required_outcomes = {'succeeded', 'blocked', 'failed'}
            elif status in {'blocked', 'failed'}:
                required_outcomes = {status}
        if kind == 'schedule':
            from .explain import validate_schedule
            if (active_capability is not None or previous_kind != 'goal' or last_goal is None
                    or last_goal['status'] != 'unmet' or payload.get('step') != last_goal['step']):
                raise ValueError('Malformed scheduling event order')
            validate_schedule(payload)
            current_schedule = payload
            schedule_index = 0
            if payload['stop'] is not None or not payload['selected']:
                required_outcomes = {'blocked', 'failed'}
        if kind == 'capability_started' and not isinstance(payload.get('capability'), str):
            raise ValueError('Malformed capability evidence')
        if kind == 'capability_finished' and (not isinstance(payload.get('capability'), str) or payload.get('status') not in {'succeeded', 'blocked', 'failed', 'retry'}):
            raise ValueError('Malformed capability evidence')
        if kind == 'outcome':
            terminal = payload.get('status')
            if terminal not in {'succeeded', 'blocked', 'failed'} or dumps(payload.get('state')) != dumps(state):
                raise ValueError('Malformed terminal outcome')
            if required_outcomes is not None and terminal not in required_outcomes:
                raise ValueError('Terminal outcome contradicts capability or goal result')
            if terminal == 'succeeded' and (
                    previous_kind != 'goal' or last_goal is None or last_goal['status'] != 'met'
                    or last_goal['state'] != dumps(state) or type(payload.get('steps')) is not int
                    or payload['steps'] != last_goal['step'] or payload.get('blocker') is not None):
                raise ValueError('Success requires the terminal goal check for the final state')
        previous_kind = kind
        decoded.append({'seq': index, 'kind': kind, 'elapsed_ms': event['elapsed_ms'], 'payload': payload})
    if row['status'] != (terminal or 'running'):
        raise ValueError('Incomplete terminal evidence')
    return {'state': state, 'run_id': row['run_id'], 'status': row['status'], 'events': decoded}


def diff_runs(db_path, left_id, right_id) -> dict:
    """Compare the final recorded values of two runs without executing code."""
    left, right = replay_run(db_path, left_id)['state'], replay_run(db_path, right_id)['state']
    return {'left_id': left_id, 'right_id': right_id,
            'added': {k: right[k] for k in sorted(right.keys() - left.keys())},
            'removed': {k: left[k] for k in sorted(left.keys() - right.keys())},
            'changed': {k: {'left': left[k], 'right': right[k]} for k in sorted(left.keys() & right.keys()) if dumps(left[k]) != dumps(right[k])}}
