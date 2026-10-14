from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Mapping
import json
import hashlib
from .merge import WriteSpec, validate_value, MergeMode, merge_json_values

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


class Workspace:
    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        self._store = {k: Entry(deepcopy(v), 1, 'init') for k, v in (initial or {}).items()}

    def snapshot(self) -> Snapshot:
        return Snapshot(data=self._store)

    def cas_commit(self, writes: Dict[FieldKey, Any], *,
                   write_specs: Dict[str, WriteSpec] | None = None,
                   provenance_id: str) -> Snapshot:
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
            if spec is not None:
                # Validators cannot retain or mutate the value to be published.
                validate_value(spec, deepcopy(value))
            staged[key] = Entry(value, cur.version + 1 if cur else 1, provenance_id)
        self._store.update(staged)
        return self.snapshot()
