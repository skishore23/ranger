"""Errors for capability declaration violations (not a Python sandbox)."""
from __future__ import annotations


class ContractViolation(RuntimeError):
    def __init__(self, capability: str, operation: str, keys):
        self.capability = capability
        self.operation = operation
        self.keys = tuple(sorted(keys))
        super().__init__(f"Capability {capability} attempted undeclared {operation}: {list(self.keys)}")


class FieldContractViolation(RuntimeError):
    """An inadmissible field, writer or value; diagnostics omit field values."""

    def __init__(self, field, writer, reason: str, *, path=()):
        self.field = field
        self.writer = writer
        self.reason = reason
        self.path = tuple(path)
        super().__init__(f'Field {field!r} rejected for writer {writer!r}: {reason}; path={self.path!r}')
