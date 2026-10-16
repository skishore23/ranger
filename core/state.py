"""Immutable workspace entries, defensive snapshots and declared read views."""
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, Mapping

from .contracts import ContractViolation

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
