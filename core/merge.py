from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class MergeMode(str, Enum):
    """Supported merge semantics for workspace writes.

    Keep minimal surface for now: default to SET. Additional modes can be added
    incrementally without changing call sites.
    """

    SET = "SET"
    MERGE_JSON = "MERGE_JSON"
    # REDUCE = "REDUCE"            # planned
    # LWW = "LWW"                  # planned (last write wins)


Validator = Callable[[Any], None]


@dataclass(frozen=True)
class WriteSpec:
    """Specification for how to write a given field.

    - merge_mode controls how new values are combined with existing ones.
    - validator, when provided, must raise on invalid values.
    - reducer reserved for future REDUCE mode.
    """

    merge_mode: MergeMode = MergeMode.SET
    validator: Optional[Validator] = None
    reducer: Optional[Callable[[Any, Any], Any]] = None


def validate_value(spec: WriteSpec, value: Any) -> None:
    if spec.validator is None:
        return
    spec.validator(value)


def merge_json_values(existing: Any, incoming: Any) -> Any:
    """Merge JSON-like values with simple, deterministic rules.

    - Dict + Dict: shallow merge; for key 'files' with list values, merge by
      path if items are objects containing 'path' and 'content'. Last writer wins.
    - List + List: concatenate.
    - Otherwise: incoming overwrites existing.
    """
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged: dict[str, Any] = dict(existing)
        for k, v in incoming.items():
            if k == "files" and isinstance(merged.get(k), list) and isinstance(v, list):
                by_path: dict[str, Any] = {}
                for item in merged[k]:
                    if isinstance(item, dict) and "path" in item:
                        by_path[str(item["path"])] = item
                for item in v:
                    if isinstance(item, dict) and "path" in item:
                        by_path[str(item["path"])] = item
                merged[k] = list(by_path.values())
            else:
                merged[k] = v
        return merged
    if isinstance(existing, list) and isinstance(incoming, list):
        return list(existing) + list(incoming)
    return incoming


