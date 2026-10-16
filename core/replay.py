"""Validate and reconstruct recorded execution in one data-only pass."""
from __future__ import annotations

import math

from .serialization import dumps, loads
from .transition_records import validate_record, validate_start


def validate_run(run, events) -> dict:
    """Validate one complete database read, including incomplete running runs."""
    if run['version'] not in (1, 2) or len(events) != run['event_count'] or not events:
        raise ValueError('Malformed execution run')
    state, decoded, terminal = {}, [], None
    active_capability = None
    required_outcomes = None
    last_goal = None
    previous_elapsed = -1
    previous_kind = None
    current_schedule = None
    schedule_index = 0
    entries, active_start, attempt, committed = {}, None, 0, False
    allowed = {'initial', 'goal', 'schedule', 'capability_started', 'capability_finished', 'commit', 'memory_atom', 'outcome'}
    for index, event in enumerate(events):
        try:
            payload = loads(event['payload'])
        except (ValueError, TypeError) as exc:
            raise ValueError('Malformed execution event JSON') from exc
        kind = event['kind']
        if (event['seq'] != index or kind not in allowed or not isinstance(payload, dict)
                or type(event['elapsed_ms']) not in (int, float) or not math.isfinite(event['elapsed_ms']) or event['elapsed_ms'] < previous_elapsed
                or terminal is not None or (index == 0) != (kind == 'initial')):
            raise ValueError('Malformed execution event sequence')
        if required_outcomes is not None and kind != 'outcome':
            raise ValueError('Execution continued after a terminal capability or goal result')
        if run['version'] == 2 and 'transition' in payload and kind not in {'commit', 'capability_finished'}:
            raise ValueError('Transition metadata attached to an unrelated event')
        previous_elapsed = event['elapsed_ms']
        if kind == 'capability_started':
            if active_capability is not None or not isinstance(payload.get('capability'), str):
                raise ValueError('Malformed capability start')
            active_capability = payload['capability']
            if run['version'] == 2:
                attempt += 1
                validate_start(payload, entries, attempt)
                active_start, committed = payload, False
            if current_schedule is not None:
                selected = current_schedule['selected']
                if schedule_index >= len(selected) or active_capability != selected[schedule_index]:
                    raise ValueError('Capability contradicts scheduling evidence')
                schedule_index += 1
        if kind in {'commit', 'capability_finished'}:
            if active_capability is None or payload.get('capability') != active_capability:
                raise ValueError('Malformed capability event order')
            if run['version'] == 2:
                if kind == 'commit':
                    if committed:
                        raise ValueError('Duplicate committed transition')
                    writes = payload.get('writes')
                    if not isinstance(writes, dict):
                        raise ValueError('Malformed transition writes')
                    entries = validate_record(payload.get('transition'), active_start, entries, 'committed', writes)
                    committed = True
                elif committed:
                    if payload.get('status') != 'succeeded' or 'transition' in payload:
                        raise ValueError('Contradictory transition outcome')
                else:
                    expected = 'unchanged' if payload.get('status') == 'succeeded' else 'rejected'
                    validate_record(payload.get('transition'), active_start, entries, expected)
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
            if kind == 'initial':
                entries = {key: {'value': value, 'version': 1, 'provenance_id': 'init'} for key, value in state.items()}
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
            last_goal = payload
            if status == 'met':
                # A met goal ends solve; a late deadline can still block its outcome.
                required_outcomes = {'succeeded', 'blocked', 'failed'}
            elif status in {'blocked', 'failed'}:
                required_outcomes = {status}
        if kind == 'schedule':
            if (active_capability is not None or previous_kind != 'goal' or last_goal is None
                    or last_goal['status'] != 'unmet' or payload.get('step') != last_goal['step']):
                raise ValueError('Malformed scheduling event order')
            validate_schedule(payload)
            current_schedule = payload
            schedule_index = 0
            if payload['stop'] is not None or not payload['selected']:
                required_outcomes = {'blocked', 'failed'}
        if kind == 'capability_finished' and payload.get('status') not in {'succeeded', 'blocked', 'failed', 'retry'}:
            raise ValueError('Malformed capability evidence')
        if kind == 'outcome':
            terminal = payload.get('status')
            if terminal not in {'succeeded', 'blocked', 'failed'} or dumps(payload.get('state')) != dumps(state):
                raise ValueError('Malformed terminal outcome')
            if required_outcomes is not None and terminal not in required_outcomes:
                raise ValueError('Terminal outcome contradicts capability or goal result')
            if terminal == 'succeeded' and (
                    previous_kind != 'goal' or last_goal is None or last_goal['status'] != 'met'
                    or type(payload.get('steps')) is not int
                    or payload['steps'] != last_goal['step'] or payload.get('blocker') is not None):
                raise ValueError('Success requires the terminal goal check for the final state')
        previous_kind = kind
        decoded.append({'seq': index, 'kind': kind, 'elapsed_ms': event['elapsed_ms'], 'payload': payload})
    if run['status'] != (terminal or 'running'):
        raise ValueError('Incomplete terminal evidence')
    return {'state': state, 'run_id': run['run_id'], 'status': run['status'], 'events': decoded,
            'record_version': run['version']}


def _strings(value):
    return (isinstance(value, list) and all(isinstance(item, str) and item for item in value)
            and len(value) == len(set(value)))


def validate_schedule(payload):
    """Reject contradictory or malformed scheduling records during replay."""
    def fail():
        raise ValueError('Malformed scheduling evidence')

    if (type(payload.get('step')) is not int or payload['step'] < 0
            or not _strings(payload.get('selected')) or not isinstance(payload.get('capabilities'), list)):
        fail()
    stop = payload.get('stop')
    stops = {'budget', 'step_limit', 'retries_exhausted', 'no_progress'}
    if stop is not None and (not isinstance(stop, dict) or not isinstance(stop.get('reason'), str)
                             or stop['reason'] not in stops
                             or not isinstance(stop.get('details'), dict) or payload['selected']):
        fail()
    identifiers, selected = set(), set()
    reasons = {'waiting': {'missing_inputs'}, 'idle': {'inputs_unchanged'},
               'selected': {'never_run', 'inputs_changed', 'retry_pending'},
               'deferred': {'conflict'}, 'blocked': stops}
    for row in payload['capabilities']:
        if not isinstance(row, dict):
            fail()
        identifier, status = row.get('capability'), row.get('status')
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or not isinstance(status, str) or status not in reasons
                or not isinstance(row.get('reason'), str) or row['reason'] not in reasons[status]
                or not _strings(row.get('missing')) or not isinstance(row.get('conflicts'), list)
                or type(row.get('attempts')) is not int or row['attempts'] < 0):
            fail()
        identifiers.add(identifier)
        if (status == 'waiting') != bool(row['missing']):
            fail()
        if (status == 'deferred') != bool(row['conflicts']):
            fail()
        if status == 'blocked' and (stop is None or row['reason'] != stop['reason']):
            fail()
        if status == 'selected':
            selected.add(identifier)
        for conflict in row['conflicts']:
            if (not isinstance(conflict, dict) or conflict.get('capability') not in payload['selected']
                    or not _strings(conflict.get('keys')) or not conflict['keys']):
                fail()
    if selected != set(payload['selected']) or (stop is not None and selected):
        fail()
