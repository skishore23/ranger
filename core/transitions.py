"""Read-only trajectory and field-history projections over validated recordings."""
from __future__ import annotations

from copy import deepcopy

from .serialization import dumps
from .trace import replay_run

# Retain earlier helper imports while implementation dependencies point inward.
from .transition_records import (
    input_refs as input_refs,
    transition_record as transition_record,
    validate_record as validate_record,
    validate_start as validate_start,
)


def _native_record(version, event):
    if version == 2 and event['kind'] in {'commit', 'capability_finished'}:
        return event['payload'].get('transition')
    return None


def _located(record, event):
    return {**deepcopy(record), 'seq': event['seq'], 'elapsed_ms': event['elapsed_ms']}


def trajectory_run(db_path, run_id=None):
    run = replay_run(db_path, run_id)
    records = []
    for event in run['events']:
        record = _native_record(run['record_version'], event)
        if record is not None:
            records.append(_located(record, event))
    return {'run_id': run['run_id'], 'status': run['status'], 'record_version': run['record_version'],
            'transitions': records, 'goals': [deepcopy(e['payload']) for e in run['events'] if e['kind'] == 'goal']}


def field_history(db_path, field, run_id=None):
    if not isinstance(field, str) or not field:
        raise ValueError('Field must be a non-empty string')
    run = replay_run(db_path, run_id)
    entries, rejections = [], []
    for event in run['events']:
        payload, kind = event['payload'], event['kind']
        record = _native_record(run['record_version'], event)
        if kind == 'initial' and field in payload['state']:
            entries.append({'version': 1, 'value': deepcopy(payload['state'][field]), 'writer': None,
                            'provenance_id': 'init', 'changed': True, 'transition_id': None, 'inputs': None,
                            'legacy': run['record_version'] == 1, 'seq': event['seq'], 'elapsed_ms': event['elapsed_ms']})
        elif kind == 'commit' and field in payload['writes']:
            if record is not None:
                change = record['changes'][field]
                entry = {**deepcopy(change['after']), 'writer': record['writer'], 'changed': change['changed'],
                         'transition_id': record['id'], 'inputs': deepcopy(record['inputs']), 'legacy': False}
            else:
                value = payload['writes'][field]
                entry = {'version': entries[-1]['version'] + 1 if entries else 1, 'value': deepcopy(value),
                         'writer': payload['capability'], 'provenance_id': None,
                         'changed': not entries or dumps(entries[-1]['value']) != dumps(value),
                         'transition_id': None, 'inputs': None, 'legacy': True}
            entries.append({**entry, 'seq': event['seq'], 'elapsed_ms': event['elapsed_ms']})
        if record is not None and record['status'] == 'rejected' and field in (
                record['declared_writes'] if record['proposed_keys'] is None else record['proposed_keys']):
            rejections.append(_located(record, event))
    return {'run_id': run['run_id'], 'field': field, 'record_version': run['record_version'],
            'entries': entries, 'rejections': rejections}
