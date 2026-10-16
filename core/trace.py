"""Durable execution evidence. Replay is data-only and opens SQLite read-only.

State supports only JSON null/bool/int/finite float/string/list/string-keyed dict.
Tuples, sets, non-string keys, custom objects and non-finite numbers are rejected
before publication. Disable tracing explicitly for applications requiring them.
Recording errors propagate; interrupted runs retain status ``running`` and cannot
certify success. This local log is evidence, not a tamper-proof audit service.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .serialization import dumps
from .replay import validate_run


_active = ContextVar('ranger_trace', default=None)


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
    def __init__(self, db_path, initial, domain=None, *, version=1):
        if version not in (1, 2) or type(version) is not int:
            raise ValueError('Unsupported execution record version')
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
                self.connection.execute("INSERT INTO execution_runs (run_id,version,started_ns,status,event_count,domain) VALUES (?,?,?,'running',0,?)",
                                        (self.run_id, version, time.time_ns(), domain))
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
    return validate_run(row, events)


def diff_runs(db_path, left_id, right_id) -> dict:
    """Compare the final recorded values of two runs without executing code."""
    left, right = replay_run(db_path, left_id)['state'], replay_run(db_path, right_id)['state']
    return {'left_id': left_id, 'right_id': right_id,
            'added': {k: right[k] for k in sorted(right.keys() - left.keys())},
            'removed': {k: left[k] for k in sorted(left.keys() - right.keys())},
            'changed': {k: {'left': left[k], 'right': right[k]} for k in sorted(left.keys() & right.keys()) if dumps(left[k]) != dumps(right[k])}}
