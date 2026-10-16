from __future__ import annotations
from copy import deepcopy
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Mapping
import json
import hashlib
from .merge import WriteSpec, validate_value, MergeMode, merge_json_values
from .contracts import ContractViolation
from .fields import _FieldContracts
from .transitions import _TransitionChecks

FieldKey = str


@dataclass(frozen=True)
class Entry:
    value: Any
    version: int
    provenance_id: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, init=False)
class Snapshot:
    _data: Mapping[FieldKey, Entry] = field(repr=False)

    def __init__(self, data: Mapping[FieldKey, Entry]):
        object.__setattr__(self, '_data', deepcopy(dict(data)))

    @property
    def data(self) -> Mapping[FieldKey, Entry]:
        return deepcopy(self._data)

    def get(self, key: FieldKey, default=None):
        ent = self._data.get(key)
        return deepcopy(ent.value) if ent is not None else default

    def exists(self, key: FieldKey) -> bool:
        return key in self._data

    def __contains__(self, key: FieldKey) -> bool:
        return self.exists(key)

    def __getitem__(self, key: FieldKey):
        return self.value(key)

    def value(self, key: FieldKey):
        return deepcopy(self._data[key].value)

    def digest(self) -> str:
        items = [(k, self._data[k].version, self._data[k].value) for k in sorted(self._data)]
        payload = json.dumps(items, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def with_read_contract(self, capability: str, reads) -> Snapshot:
        """Export only declared inputs and reject explicit undeclared accesses."""
        return _ContractSnapshot(self, capability, reads)


class _DeclaredEntries(MappingABC):
    def __init__(self, data, capability, reads):
        self._entries = deepcopy(data)
        self._capability = capability
        self._reads = reads

    def __getitem__(self, key):
        if key not in self._reads:
            raise ContractViolation(self._capability, 'reads', [key])
        return self._entries[key]

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)


class _ContractSnapshot(Snapshot):
    def __init__(self, snapshot, capability, reads):
        allowed = frozenset(reads)
        super().__init__({key: entry for key, entry in snapshot._data.items() if key in allowed})
        object.__setattr__(self, '_capability', capability)
        object.__setattr__(self, '_reads', allowed)

    def _check_read(self, key):
        if key not in self._reads:
            raise ContractViolation(self._capability, 'reads', [key])

    def get(self, key, default=None):
        self._check_read(key)
        return super().get(key, default)

    def value(self, key):
        self._check_read(key)
        return super().value(key)

    def exists(self, key):
        self._check_read(key)
        return super().exists(key)

    @property
    def data(self):
        return _DeclaredEntries(self._data, self._capability, self._reads)


class Workspace:
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
        if staged:
            from .contracts import TransitionRejected
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
