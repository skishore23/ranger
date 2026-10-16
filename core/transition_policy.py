"""Opt-in pre-publication checks over previous and complete proposed state."""
from __future__ import annotations

from collections.abc import Mapping

from .contracts import TransitionRejected
from .errors import GoalCheck


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
