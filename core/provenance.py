from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping


def _stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest_reads(reads: Mapping[str, Any]) -> str:
    items = [(k, reads.get(k, None)) for k in sorted(reads.keys())]
    return _stable_hash(items)


def digest_writes(writes: Mapping[str, Any]) -> str:
    items = [(k, writes.get(k, None)) for k in sorted(writes.keys())]
    return _stable_hash(items)


@dataclass(frozen=True)
class Provenance:
    capability_id: str
    inputs_digest: str
    outputs_digest: str
    runner: str
    meta: Dict[str, Any]


