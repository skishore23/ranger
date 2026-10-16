"""Opt-in shared contracts for workspace initialization and publication.

Contracts constrain JSON values and trusted capability identifiers. They do not
authenticate callers or sandbox Python. Keep differently owned values in
separate workspace keys; permissions apply to a complete key, not nested paths.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from .contracts import FieldContractViolation
from .serialization import dumps


def _validator(schema):
    if isinstance(schema, dict) and '$schema' in schema:
        validator = validator_for(schema, default=None)
        if validator is None:
            raise ValueError('Unsupported JSON Schema draft')
    else:
        validator = Draft202012Validator
    try:
        validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError('Invalid field JSON Schema') from exc
    # An empty registry has no remote retrieval. Local references still resolve.
    return validator(schema, registry=Registry())


@dataclass(frozen=True)
class FieldContract:
    """Shared JSON Schema and complete-key write policy.

    allowed_writers contains exact capability IDs. Empty means no updates.
    allow_initial separately permits caller-supplied seeds, defaulting to False.
    Fields need not exist until supplied or produced; schemas apply when present.
    """

    schema: dict | bool
    allowed_writers: frozenset[str] = field(default_factory=frozenset)
    allow_initial: bool = False

    def __post_init__(self):
        if type(self.schema) not in (dict, bool):
            raise TypeError('Field schema must be a JSON Schema object or boolean')
        dumps(self.schema)
        schema = deepcopy(self.schema)
        _validator(schema)
        if isinstance(self.allowed_writers, (str, bytes)):
            raise TypeError('allowed_writers must be a collection of capability IDs')
        writers = frozenset(self.allowed_writers)
        if any(not isinstance(writer, str) or not writer.strip() for writer in writers):
            raise ValueError('allowed_writers must contain non-empty capability IDs')
        if type(self.allow_initial) is not bool:
            raise TypeError('allow_initial must be a bool')
        object.__setattr__(self, 'schema', schema)
        object.__setattr__(self, 'allowed_writers', writers)


class _FieldContracts:
    """Privately owned schemas and compiled validators for one engine/workspace."""

    def __init__(self, contracts):
        if not isinstance(contracts, Mapping):
            raise TypeError('field_contracts must be a mapping or None')
        self._contracts = {}
        self._validators = {}
        for key, contract in contracts.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError('Field names must be non-empty strings')
            if not isinstance(contract, FieldContract):
                raise TypeError('Each field contract must be a FieldContract')
            captured = FieldContract(contract.schema, contract.allowed_writers, contract.allow_initial)
            self._contracts[key] = captured
            self._validators[key] = _validator(captured.schema)

    def export(self):
        return deepcopy(self._contracts)

    def _get(self, key, writer):
        if key not in self._contracts:
            raise FieldContractViolation(key, writer, 'unknown_field')
        return self._contracts[key]

    def check_reads(self, keys, writer):
        for key in sorted(keys):
            self._get(key, writer)

    def authorize(self, keys, writer, *, initial=False):
        for key in keys:
            contract = self._get(key, writer)
            if initial:
                if not contract.allow_initial:
                    raise FieldContractViolation(key, writer, 'initial_not_allowed')
            elif writer not in contract.allowed_writers:
                raise FieldContractViolation(key, writer, 'writer_not_allowed')

    def validate_json(self, values, writer):
        for key, value in values.items():
            self._get(key, writer)
            try:
                dumps(value)
            except (TypeError, ValueError, RecursionError):
                raise FieldContractViolation(key, writer, 'schema') from None

    def validate_values(self, values, writer):
        self.validate_json(values, writer)
        for key, value in values.items():
            try:
                self._validators[key].validate(value)
            except ValidationError as exc:
                raise FieldContractViolation(key, writer, 'schema', path=exc.absolute_path) from None
            except (TypeError, ValueError, Unresolvable, RecursionError):
                raise FieldContractViolation(key, writer, 'schema') from None
