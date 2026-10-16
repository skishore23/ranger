"""Canonical strict JSON shared by state contracts and persisted evidence.

This layer has no dependency on execution, recording, SQLite or providers.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def dumps(value: Any) -> str:
    """Encode supported values without coercion or silently losing type information."""
    ancestors = set()

    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) in (list, dict):
            if id(item) in ancestors:
                raise TypeError('Unsupported circular JSON value')
            if type(item) is dict and any(type(k) is not str for k in item):
                raise TypeError('JSON object keys must be strings')
            ancestors.add(id(item))
            for child in (item.values() if type(item) is dict else item):
                check(child)
            ancestors.remove(id(item))
            return
        raise TypeError(f'Unsupported JSON value: {type(item).__name__}; use finite JSON values or trace=False')
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def loads(payload: str) -> Any:
    """Decode strict JSON, rejecting duplicate object keys and non-finite values."""
    value = json.loads(payload, object_pairs_hook=_unique_object)
    dumps(value)
    return value


def fingerprint(value: Any) -> str:
    """SHA-256 of canonical strict JSON, independent of recording settings."""
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()
