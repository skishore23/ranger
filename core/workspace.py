from __future__ import annotations
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


@dataclass(frozen=True)
class Snapshot:
    data: Mapping[FieldKey, Entry]

    def get(self, key: FieldKey, default=None):
        ent = self.data.get(key)
        return ent.value if ent is not None else default

    def exists(self, key: FieldKey) -> bool:
        return key in self.data

    def value(self, key: FieldKey):
        ent = self.data.get(key)
        if ent is None:
            raise KeyError(key)
        return ent.value

    def digest(self) -> str:
        # Stable hash over visible state (keys -> value + version)
        items = []
        for k in sorted(self.data.keys()):
            ent = self.data[k]
            items.append((k, ent.version, ent.value))
        payload = json.dumps(items, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Workspace:
    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        self._store: Dict[FieldKey, Entry] = {}
        if initial:
            for k, v in initial.items():
                self._store[k] = Entry(value=v, version=1, provenance_id="init")

    def snapshot(self) -> Snapshot:
        return Snapshot(data=dict(self._store))

    def cas_commit(
        self,
        writes: Dict[FieldKey, Any],
        *,
        write_specs: Dict[str, WriteSpec] | None = None,
        provenance_id: str,
    ) -> Snapshot:
        # Fail-fast if writes include keys not covered by write_specs when provided
        if write_specs is not None:
            undeclared = set(writes.keys()) - set(write_specs.keys())
            if undeclared:
                raise RuntimeError(f"undeclared write_specs for: {sorted(undeclared)}")

        for k, v in writes.items():
            spec = write_specs.get(k) if isinstance(write_specs, dict) else None
            if spec is not None:
                validate_value(spec, v)
            cur = self._store.get(k)
            next_version = (cur.version + 1) if cur is not None else 1
            if spec is not None and spec.merge_mode == MergeMode.MERGE_JSON and cur is not None:
                merged = merge_json_values(cur.value, v)
                self._store[k] = Entry(value=merged, version=next_version, provenance_id=provenance_id)
            else:
                self._store[k] = Entry(value=v, version=next_version, provenance_id=provenance_id)
        return self.snapshot()
