"""Validate scheduling evidence independently of diagnostics and persistence."""
from __future__ import annotations


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
