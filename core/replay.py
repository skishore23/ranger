"""Validate and reconstruct execution evidence without storage or workflow code."""
from __future__ import annotations

import math

from .schedule_records import validate_schedule
from .serialization import dumps, loads
from .transition_records import TransitionReplay


_EVENT_KINDS = frozenset({
    'initial', 'goal', 'schedule', 'capability_started', 'capability_finished',
    'commit', 'memory_atom', 'outcome',
})
_TERMINAL_STATUSES = frozenset({'succeeded', 'blocked', 'failed'})


def validate_run(run, events) -> dict:
    """Validate one complete database read, including incomplete running runs."""
    if run['version'] not in (1, 2) or len(events) != run['event_count'] or not events:
        raise ValueError('Malformed execution run')
    replay = _RunReplay(run['version'])
    decoded = [replay.accept(index, event) for index, event in enumerate(events)]
    if run['status'] != (replay.terminal or 'running'):
        raise ValueError('Incomplete terminal evidence')
    return {'state': replay.state, 'run_id': run['run_id'], 'status': run['status'],
            'events': decoded, 'record_version': run['version']}


class _RunReplay:
    """One run's event lifecycle; handlers own each event's invariants."""

    def __init__(self, version):
        self.state = {}
        self.terminal = None
        self._active_capability = None
        self._required_outcomes = None
        self._last_goal = None
        self._previous_elapsed = -1
        self._previous_kind = None
        self._schedule = None
        self._schedule_index = 0
        self._transitions = TransitionReplay() if version == 2 else None

    def accept(self, index, event):
        try:
            payload = loads(event['payload'])
        except (ValueError, TypeError) as exc:
            raise ValueError('Malformed execution event JSON') from exc
        kind, elapsed = event['kind'], event['elapsed_ms']
        if (event['seq'] != index or kind not in _EVENT_KINDS or not isinstance(payload, dict)
                or type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                or elapsed < self._previous_elapsed or self.terminal is not None
                or (index == 0) != (kind == 'initial')):
            raise ValueError('Malformed execution event sequence')
        if self._required_outcomes is not None and kind != 'outcome':
            raise ValueError('Execution continued after a terminal capability or goal result')
        if self._transitions is not None and 'transition' in payload and kind not in {'commit', 'capability_finished'}:
            raise ValueError('Transition metadata attached to an unrelated event')
        self._previous_elapsed = elapsed
        match kind:
            case 'initial':
                self._initial(payload)
            case 'goal':
                self._goal(payload)
            case 'schedule':
                self._scheduling(payload)
            case 'capability_started':
                self._start_capability(payload)
            case 'capability_finished':
                self._finish_capability(payload)
            case 'commit':
                self._commit(payload)
            case 'memory_atom':
                self._memory(payload)
            case 'outcome':
                self._outcome(payload)
        self._previous_kind = kind
        return {'seq': index, 'kind': kind, 'elapsed_ms': elapsed, 'payload': payload}

    def _require_idle(self):
        if self._active_capability is not None:
            raise ValueError('Unfinished capability evidence')

    def _require_active(self, payload):
        if self._active_capability is None or payload.get('capability') != self._active_capability:
            raise ValueError('Malformed capability event order')

    def _initial(self, payload):
        snapshot = payload.get('state')
        if not isinstance(snapshot, dict):
            raise ValueError('Malformed state snapshot')
        self.state = snapshot
        if self._transitions is not None:
            self._transitions.initialize(snapshot)

    def _start_capability(self, payload):
        capability = payload.get('capability')
        if self._active_capability is not None or not isinstance(capability, str):
            raise ValueError('Malformed capability start')
        self._active_capability = capability
        if self._transitions is not None:
            self._transitions.start(payload)
        if self._schedule is not None:
            selected = self._schedule['selected']
            if self._schedule_index >= len(selected) or capability != selected[self._schedule_index]:
                raise ValueError('Capability contradicts scheduling evidence')
            self._schedule_index += 1

    def _commit(self, payload):
        self._require_active(payload)
        if self._transitions is not None:
            self._transitions.commit(payload)
        snapshot, writes = payload.get('state'), payload.get('writes')
        if not isinstance(snapshot, dict):
            raise ValueError('Malformed state snapshot')
        if not isinstance(writes, dict) or dumps({**self.state, **writes}) != dumps(snapshot):
            raise ValueError('Inconsistent committed state')
        self.state = snapshot

    def _finish_capability(self, payload):
        self._require_active(payload)
        if self._transitions is not None:
            self._transitions.finish(payload)
        duration, status = payload.get('duration_ms'), payload.get('status')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError('Malformed capability duration')
        if status in {'failed', 'blocked'}:
            self._required_outcomes = {status}
        self._active_capability = None
        if not isinstance(payload.get('capability'), str) or status not in {'succeeded', 'blocked', 'failed', 'retry'}:
            raise ValueError('Malformed capability evidence')

    def _goal(self, payload):
        self._require_idle()
        if self._previous_kind == 'goal':
            raise ValueError('Duplicate goal evidence')
        if self._schedule is not None and self._schedule_index != len(self._schedule['selected']):
            raise ValueError('Goal checked before scheduled work completed')
        status, step = payload.get('status'), payload.get('step')
        if status not in {'met', 'unmet', 'blocked', 'failed'} or type(step) is not int or step < 0:
            raise ValueError('Malformed goal evidence')
        if self._last_goal is not None and step <= self._last_goal['step']:
            raise ValueError('Non-increasing goal step')
        for field in ('missing', 'unmet'):
            if field in payload and (not isinstance(payload[field], list)
                    or any(not isinstance(item, str) or not item for item in payload[field])):
                raise ValueError('Malformed goal requirements')
        if status == 'met' and (payload.get('missing') or payload.get('unmet')):
            raise ValueError('Met goal has unmet requirements')
        self._schedule, self._schedule_index = None, 0
        self._last_goal = {'status': status, 'step': step, 'state': dumps(self.state)}
        if status == 'met':
            # A late deadline can still block a run after its final met goal.
            self._required_outcomes = _TERMINAL_STATUSES
        elif status in {'blocked', 'failed'}:
            self._required_outcomes = {status}

    def _scheduling(self, payload):
        if (self._active_capability is not None or self._previous_kind != 'goal' or self._last_goal is None
                or self._last_goal['status'] != 'unmet' or payload.get('step') != self._last_goal['step']):
            raise ValueError('Malformed scheduling event order')
        validate_schedule(payload)
        self._schedule, self._schedule_index = payload, 0
        if payload['stop'] is not None or not payload['selected']:
            self._required_outcomes = {'blocked', 'failed'}

    @staticmethod
    def _memory(payload):
        atom = payload.get('atom')
        if (not isinstance(payload.get('domain'), str) or not isinstance(atom, dict)
                or not {'id', 'modality', 'schema', 'content', 'facets', 'provenance', 'policy'} <= atom.keys()
                or not isinstance(atom['id'], str) or not isinstance(atom['modality'], str)
                or not all(isinstance(atom[key], dict) for key in ('facets', 'provenance', 'policy'))
                or type(payload.get('timestamp')) is not int):
            raise ValueError('Malformed memory evidence')

    def _outcome(self, payload):
        self._require_idle()
        terminal = payload.get('status')
        if terminal not in _TERMINAL_STATUSES or dumps(payload.get('state')) != dumps(self.state):
            raise ValueError('Malformed terminal outcome')
        if self._required_outcomes is not None and terminal not in self._required_outcomes:
            raise ValueError('Terminal outcome contradicts capability or goal result')
        if terminal == 'succeeded' and (
                self._previous_kind != 'goal' or self._last_goal is None or self._last_goal['status'] != 'met'
                or self._last_goal['state'] != dumps(self.state) or type(payload.get('steps')) is not int
                or payload['steps'] != self._last_goal['step'] or payload.get('blocker') is not None):
            raise ValueError('Success requires the terminal goal check for the final state')
        self.terminal = terminal
