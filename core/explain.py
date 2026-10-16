"""Read-only scheduling diagnostics built exclusively from recorded evidence."""
from __future__ import annotations

from .schedule_records import validate_schedule as validate_schedule
from .trace import replay_run


def explain_run(db_path, run_id=None):
    """Explain the selected run without importing or executing its workflow."""
    run = replay_run(db_path, run_id)
    goals = [event['payload'] for event in run['events'] if event['kind'] == 'goal']
    schedules = [event['payload'] for event in run['events'] if event['kind'] == 'schedule']
    outcomes = [event['payload'] for event in run['events'] if event['kind'] == 'outcome']
    outcome = outcomes[-1] if outcomes else {}
    return {'run_id': run['run_id'], 'status': run['status'],
            'goal': goals[-1] if goals else None, 'schedules': schedules,
            'blocker': outcome.get('blocker'), 'error': outcome.get('error')}
