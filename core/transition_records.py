"""Build and validate native transition records without executing workflow code."""
from __future__ import annotations

from copy import deepcopy
import re

from .serialization import dumps, fingerprint


TRANSITION_SCHEMA = 'ranger.transition@1'


class TransitionReplay:
    """Run-local field versions and the currently recorded capability attempt."""

    def __init__(self):
        self._entries = {}
        self._attempt = 0
        self._start = None
        self._committed = False

    def initialize(self, state):
        self._entries = {key: {'value': value, 'version': 1, 'provenance_id': 'init'}
                         for key, value in state.items()}

    def start(self, payload):
        self._attempt += 1
        validate_start(payload, self._entries, self._attempt)
        self._start, self._committed = payload, False

    def commit(self, payload):
        if self._committed:
            raise ValueError('Duplicate committed transition')
        writes = payload.get('writes')
        if not isinstance(writes, dict):
            raise ValueError('Malformed transition writes')
        self._entries = validate_record(payload.get('transition'), self._start, self._entries, 'committed', writes)
        self._committed = True

    def finish(self, payload):
        if self._committed:
            if payload.get('status') != 'succeeded' or 'transition' in payload:
                raise ValueError('Contradictory transition outcome')
        else:
            status = 'unchanged' if payload.get('status') == 'succeeded' else 'rejected'
            validate_record(payload.get('transition'), self._start, self._entries, status)


def input_refs(snapshot, keys, source=None):
    actual, original = snapshot.data, (source or snapshot).data
    return {key: {'version': original[key].version, 'provenance_id': original[key].provenance_id,
                  'sha256': fingerprint(actual[key].value), 'source_sha256': fingerprint(original[key].value)}
            for key in sorted(keys)}


def _entry(entry):
    return None if entry is None else {'value': deepcopy(entry.value), 'version': entry.version,
                                      'provenance_id': entry.provenance_id}


def transition_record(writer, attempt, inputs, declared_writes, *, status, previous=None, proposed=None,
                      proposed_keys=None, checks=(), error=None):
    changes = {}
    if status == 'committed':
        before, after = previous.data, proposed.data
        for key in proposed_keys:
            old, new = _entry(before.get(key)), _entry(after[key])
            changes[key] = {'before': old, 'after': new,
                            'changed': old is None or dumps(old['value']) != dumps(new['value'])}
    return {'schema': TRANSITION_SCHEMA, 'id': f't{attempt}', 'attempt': attempt,
            'writer': writer, 'status': status, 'inputs': deepcopy(inputs), 'changes': changes,
            'declared_writes': sorted(declared_writes), 'proposed_keys': proposed_keys,
            'checks': deepcopy(list(checks)), 'error': error}


def finished_record(writer, attempt, inputs, declared_writes, *, writes, checks=(), error=None):
    """Record an attempt that ended without a commit, preserving failure origin."""
    keys = sorted(writes) if isinstance(writes, dict) and all(isinstance(key, str) and key for key in writes) else None
    failure = None
    if error is not None:
        source = 'transition_checks' if checks and not checks[-1]['met'] else 'execution'
        failure = {'type': type(error).__name__, 'message': str(error), 'source': source}
    return transition_record(writer, attempt, inputs, declared_writes,
                             status='unchanged' if error is None else 'rejected',
                             proposed_keys=keys, checks=checks, error=failure)


def _names(value):
    return (isinstance(value, list) and all(isinstance(v, str) and v for v in value)
            and value == sorted(set(value)))


def validate_start(payload, entries, attempt):
    checks = payload.get('checks')
    if (type(payload.get('attempt')) is not int or payload['attempt'] != attempt
            or not _names(payload.get('reads')) or not _names(payload.get('writes'))
            or not isinstance(payload.get('inputs'), dict) or not isinstance(checks, list)
            or any(not isinstance(name, str) or not name for name in checks) or len(checks) != len(set(checks))):
        raise ValueError('Malformed transition attempt')
    expected = {}
    for key in payload['reads']:
        if key not in entries:
            raise ValueError('Transition consumed an unavailable input')
        entry = entries[key]
        expected[key] = {'version': entry['version'], 'provenance_id': entry['provenance_id'],
                         'sha256': fingerprint(entry['value']), 'source_sha256': fingerprint(entry['value'])}
    if dumps(payload['inputs']) != dumps(expected):
        raise ValueError('Transition input versions contradict state')


def validate_record(record, start, entries, status, writes=None):
    """Validate native records without executing callbacks, tools, or models."""
    required = {'schema', 'id', 'attempt', 'writer', 'status', 'inputs', 'changes',
                'declared_writes', 'proposed_keys', 'checks', 'error'}
    if (not isinstance(record, dict) or set(record) != required
            or record['schema'] != TRANSITION_SCHEMA or record['id'] != f"t{start['attempt']}"
            or type(record['attempt']) is not int or record['attempt'] != start['attempt']
            or record['writer'] != start['capability'] or record['status'] != status
            or record['declared_writes'] != start['writes'] or not isinstance(record['inputs'], dict)
            or set(record['inputs']) != set(start['inputs']) or not isinstance(record['changes'], dict)
            or not isinstance(record['checks'], list)):
        _invalid_record()
    _validate_inputs(record['inputs'], start['inputs'])
    check_names = _validate_checks(record['checks'], start['checks'])
    if record['proposed_keys'] is not None and not _names(record['proposed_keys']):
        _invalid_record()
    if status == 'committed':
        return _committed_entries(record, start, entries, writes, check_names)
    if record['changes']:
        _invalid_record()
    if status == 'unchanged':
        if record['error'] is not None or record['checks'] or record['proposed_keys'] != []:
            _invalid_record()
    elif status == 'rejected':
        _validate_rejection(record['error'], record['checks'])
    return entries


def _invalid_record():
    raise ValueError('Malformed transition evidence')


def _validate_inputs(inputs, expected):
    for key, ref in inputs.items():
        if (not isinstance(ref, dict) or set(ref) != {'version', 'provenance_id', 'sha256', 'source_sha256'}
                or type(ref['version']) is not int
                or any(ref[field] != expected[key][field] for field in ('version', 'provenance_id', 'source_sha256'))
                or not isinstance(ref['sha256'], str) or re.fullmatch('[0-9a-f]{64}', ref['sha256']) is None):
            _invalid_record()


def _validate_checks(checks, expected):
    check_names = []
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'name', 'met', 'unmet'}
                or not isinstance(check.get('name'), str) or type(check.get('met')) is not bool
                or not isinstance(check.get('unmet'), list)
                or any(not isinstance(item, str) or not item for item in check['unmet'])
                or (check['met'] and check['unmet']) or (not check['met'] and not check['unmet'])):
            _invalid_record()
        check_names.append(check['name'])
    # Execution follows registration order, recorded explicitly for this attempt.
    if check_names != expected[:len(check_names)] or len(check_names) != len(set(check_names)):
        _invalid_record()
    return check_names


def _committed_entries(record, start, entries, writes, check_names):
    if (record['error'] is not None or check_names != start['checks']
            or any(not check['met'] for check in record['checks'])
            or record['proposed_keys'] != sorted(writes) or not writes
            or not set(writes) <= set(start['writes']) or set(record['changes']) != set(writes)):
        _invalid_record()
    updated = deepcopy(entries)
    for key, value in writes.items():
        change = record['changes'][key]
        old = entries.get(key)
        if not isinstance(change, dict) or set(change) != {'before', 'after', 'changed'}:
            _invalid_record()
        new = change['after']
        if (not isinstance(new, dict) or set(new) != {'value', 'version', 'provenance_id'}
                or type(new['version']) is not int or new['version'] != (old['version'] + 1 if old else 1)
                or not isinstance(new['provenance_id'], str) or not new['provenance_id']
                or dumps(new['value']) != dumps(value) or dumps(change['before']) != dumps(old)
                or type(change['changed']) is not bool
                or change['changed'] != (old is None or dumps(old['value']) != dumps(value))):
            _invalid_record()
        updated[key] = deepcopy(new)
    return updated


def _validate_rejection(error, checks):
    if (not isinstance(error, dict) or set(error) != {'type', 'message', 'source'}
            or not isinstance(error['type'], str) or not error['type'] or not isinstance(error['message'], str)
            or error['source'] not in ('transition_checks', 'execution')
            or any(not row['met'] for row in checks[:-1])):
        _invalid_record()
    failed_check = bool(checks and not checks[-1]['met'])
    if ((error['source'] == 'transition_checks') != failed_check
            or (failed_check and error['type'] != 'TransitionRejected')):
        _invalid_record()
