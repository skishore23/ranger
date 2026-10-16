"""Transition policy and read-only trajectory queries over recorded evidence."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import re

from .contracts import TransitionRejected
from .trace import dumps


class _TransitionChecks:
    def __init__(self, checks=None):
        if checks is None:
            checks = {}
        if not isinstance(checks, Mapping):
            raise TypeError('transition_checks must be a mapping of names to callbacks')
        self.callbacks = dict(checks)
        if any(not isinstance(name, str) or not name.strip() or not callable(check)
               for name, check in self.callbacks.items()):
            raise ValueError('Transition checks require non-empty names and callable checks')

    def evaluate(self, previous, proposed):
        from .errors import GoalCheck
        results = []
        for name, check in self.callbacks.items():
            try:
                outcome = check(previous, proposed)
                if type(outcome) is bool:
                    met, unmet = outcome, [] if outcome else [f'Transition check {name} failed']
                elif isinstance(outcome, GoalCheck):
                    met, unmet = outcome.met, list(outcome.unmet)
                    if not met and not unmet:
                        unmet = [f'Transition check {name} failed']
                else:
                    met, unmet = False, ['Transition check must return bool or GoalCheck']
            except Exception as exc:
                met, unmet = False, [f'Transition check raised {type(exc).__name__}']
            results.append({'name': name, 'met': met, 'unmet': unmet})
            if not met:
                raise TransitionRejected(name, unmet, results)
        return results


def _hash(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def input_refs(snapshot, keys, source=None):
    actual, original = snapshot.data, (source or snapshot).data
    return {key: {'version': original[key].version, 'provenance_id': original[key].provenance_id,
                  'sha256': _hash(actual[key].value), 'source_sha256': _hash(original[key].value)}
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
    return {'schema': 'ranger.transition@1', 'id': f't{attempt}', 'attempt': attempt,
            'writer': writer, 'status': status, 'inputs': deepcopy(inputs), 'changes': changes,
            'declared_writes': sorted(declared_writes), 'proposed_keys': proposed_keys,
            'checks': deepcopy(list(checks)), 'error': error}


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
                         'sha256': _hash(entry['value']), 'source_sha256': _hash(entry['value'])}
    if dumps(payload['inputs']) != dumps(expected):
        raise ValueError('Transition input versions contradict state')


def validate_record(record, start, entries, status, writes=None):
    """Validate native records without executing callbacks, tools, or models."""
    def fail():
        raise ValueError('Malformed transition evidence')
    required = {'schema', 'id', 'attempt', 'writer', 'status', 'inputs', 'changes',
                'declared_writes', 'proposed_keys', 'checks', 'error'}
    if (not isinstance(record, dict) or set(record) != required
            or record['schema'] != 'ranger.transition@1' or record['id'] != f"t{start['attempt']}"
            or type(record['attempt']) is not int or record['attempt'] != start['attempt']
            or record['writer'] != start['capability'] or record['status'] != status
            or record['declared_writes'] != start['writes'] or not isinstance(record['inputs'], dict)
            or set(record['inputs']) != set(start['inputs']) or not isinstance(record['changes'], dict)
            or not isinstance(record['checks'], list)):
        fail()
    for key, ref in record['inputs'].items():
        if (not isinstance(ref, dict) or set(ref) != {'version', 'provenance_id', 'sha256', 'source_sha256'}
                or type(ref['version']) is not int
                or any(ref[field] != start['inputs'][key][field] for field in ('version', 'provenance_id', 'source_sha256'))
                or not isinstance(ref['sha256'], str) or re.fullmatch('[0-9a-f]{64}', ref['sha256']) is None):
            fail()
    check_names = []
    for check in record['checks']:
        if (not isinstance(check, dict) or set(check) != {'name', 'met', 'unmet'}
                or not isinstance(check.get('name'), str) or type(check.get('met')) is not bool
                or not isinstance(check.get('unmet'), list)
                or any(not isinstance(item, str) or not item for item in check['unmet'])
                or (check['met'] and check['unmet']) or (not check['met'] and not check['unmet'])):
            fail()
        check_names.append(check['name'])
    # Execution follows registration order, recorded explicitly for this attempt.
    if check_names != start['checks'][:len(check_names)] or len(check_names) != len(set(check_names)):
        fail()
    if record['proposed_keys'] is not None and not _names(record['proposed_keys']):
        fail()
    if status == 'committed':
        if (record['error'] is not None or check_names != start['checks']
                or any(not check['met'] for check in record['checks'])
                or record['proposed_keys'] != sorted(writes) or not writes
                or not set(writes) <= set(start['writes']) or set(record['changes']) != set(writes)):
            fail()
        updated = deepcopy(entries)
        for key, value in writes.items():
            change = record['changes'][key]
            old = entries.get(key)
            if not isinstance(change, dict) or set(change) != {'before', 'after', 'changed'}:
                fail()
            new = change['after']
            if (not isinstance(new, dict) or set(new) != {'value', 'version', 'provenance_id'}
                    or type(new['version']) is not int or new['version'] != (old['version'] + 1 if old else 1)
                    or not isinstance(new['provenance_id'], str) or not new['provenance_id']
                    or dumps(new['value']) != dumps(value) or dumps(change['before']) != dumps(old)
                    or type(change['changed']) is not bool
                    or change['changed'] != (old is None or dumps(old['value']) != dumps(value))):
                fail()
            updated[key] = deepcopy(new)
        return updated
    if record['changes']:
        fail()
    if status == 'unchanged':
        if record['error'] is not None or record['checks'] or record['proposed_keys'] != []:
            fail()
    elif status == 'rejected':
        error = record['error']
        if (not isinstance(error, dict) or set(error) != {'type', 'message', 'source'}
                or not isinstance(error['type'], str) or not error['type'] or not isinstance(error['message'], str)
                or error['source'] not in ('transition_checks', 'execution')
                or any(not row['met'] for row in record['checks'][:-1])):
            fail()
        failed_check = bool(record['checks'] and not record['checks'][-1]['met'])
        if ((error['source'] == 'transition_checks') != failed_check
                or (failed_check and error['type'] != 'TransitionRejected')):
            fail()
    return entries


def trajectory_run(db_path, run_id=None):
    from .trace import replay_run
    run = replay_run(db_path, run_id)
    records = []
    for event in run['events']:
        record = event['payload'].get('transition') if (run['record_version'] == 2
                 and event['kind'] in {'commit', 'capability_finished'}) else None
        if record is not None:
            records.append({**deepcopy(record), 'seq': event['seq'], 'elapsed_ms': event['elapsed_ms']})
    return {'run_id': run['run_id'], 'status': run['status'], 'record_version': run['record_version'],
            'transitions': records, 'goals': [deepcopy(e['payload']) for e in run['events'] if e['kind'] == 'goal']}


def field_history(db_path, field, run_id=None):
    from .trace import replay_run
    if not isinstance(field, str) or not field:
        raise ValueError('Field must be a non-empty string')
    run = replay_run(db_path, run_id)
    entries, rejections = [], []
    for event in run['events']:
        payload, kind = event['payload'], event['kind']
        record = payload.get('transition') if (run['record_version'] == 2
                 and kind in {'commit', 'capability_finished'}) else None
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
            rejections.append({**deepcopy(record), 'seq': event['seq'], 'elapsed_ms': event['elapsed_ms']})
    return {'run_id': run['run_id'], 'field': field, 'record_version': run['record_version'],
            'entries': entries, 'rejections': rejections}
