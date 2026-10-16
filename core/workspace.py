"""Atomic admission and publication of in-process workspace updates."""
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from copy import deepcopy
from typing import Any, Callable, Dict, Optional

from .contracts import ContractViolation as ContractViolation, TransitionRejected
from .fields import _FieldContracts
from .merge import WriteSpec, validate_value, MergeMode, merge_json_values
from .state import Entry as Entry, FieldKey as FieldKey, Snapshot as Snapshot
from .transition_policy import _TransitionChecks


class Workspace:
    """Stage, validate and publish one capability's complete write set at a time."""

    def __init__(self, initial: Optional[Dict[str, Any]] = None, *, field_contracts=None, transition_checks=None):
        self._fields = _FieldContracts(field_contracts) if field_contracts is not None else None
        self._transition_checks = _TransitionChecks(transition_checks)
        self._last_transition_checks = []
        if self._fields is not None and initial is not None and not isinstance(initial, MappingABC):
            raise TypeError('Initial state must be a mapping')
        values = deepcopy(initial or {})
        if self._fields is not None:
            self._fields.authorize(values, None, initial=True)
            self._fields.validate_values(values, None)
        self._store = {k: Entry(v, 1, 'init') for k, v in values.items()}

    def snapshot(self) -> Snapshot:
        return Snapshot(data=self._store)

    @property
    def last_transition_checks(self):
        return deepcopy(self._last_transition_checks)

    def cas_commit(self, writes: Dict[FieldKey, Any], *,
                   write_specs: Dict[str, WriteSpec] | None = None,
                   provenance_id: str,
                   writer: str | None = None,
                   before_publish: Optional[Callable[[], None]] = None) -> Snapshot:
        self._last_transition_checks = []
        if self._fields is not None:
            self._fields.authorize(writes, writer)
            self._fields.validate_json(writes, writer)
        if write_specs is not None:
            undeclared = set(writes) - set(write_specs)
            if undeclared:
                raise RuntimeError(f'undeclared write_specs for: {sorted(undeclared)}')
        staged = {}
        for key, value in writes.items():
            spec = write_specs.get(key) if write_specs is not None else None
            cur = self._store.get(key)
            value = deepcopy(value)
            if spec is not None and spec.merge_mode == MergeMode.MERGE_JSON and cur is not None:
                value = merge_json_values(deepcopy(cur.value), value)
            if self._fields is not None:
                self._fields.validate_values({key: value}, writer)
            if spec is not None:
                # Validators cannot retain or mutate the value to be published.
                validate_value(spec, deepcopy(value))
            staged[key] = Entry(value, cur.version + 1 if cur else 1, provenance_id)
        if staged and self._transition_checks.callbacks:
            try:
                self._last_transition_checks = self._transition_checks.evaluate(
                    self.snapshot(), Snapshot({**self._store, **staged}))
            except TransitionRejected as exc:
                self._last_transition_checks = deepcopy(exc.checks)
                raise
        # Validators can take arbitrary time. The engine gates publication only
        # after every value has been staged and validated; standalone callers may
        # omit the gate. A failed gate leaves all staged writes unpublished.
        if before_publish is not None:
            before_publish()
        self._store.update(staged)
        return self.snapshot()
