from __future__ import annotations

from typing import Any, Callable, Optional


def ensure(predicate: bool, message: str) -> None:
    if not predicate:
        raise ValueError(message)


def json_schema_validator(schema: dict) -> Callable[[Any], None]:
    """Return a simple validator function using jsonschema if available.

    Fail-fast: if jsonschema isn't installed, raise when invoked.
    """

    def _validate(value: Any) -> None:
        try:
            import jsonschema  # type: ignore
        except Exception as exc:
            raise RuntimeError("jsonschema not available for validation") from exc
        jsonschema.validate(instance=value, schema=schema)  # type: ignore

    return _validate


def require_keys(value: dict, required: set[str]) -> None:
    missing = required - set(value.keys())
    if missing:
        raise ValueError(f"missing required keys: {sorted(missing)}")


