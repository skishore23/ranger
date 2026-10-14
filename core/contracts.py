"""Errors for capability declaration violations (not a Python sandbox)."""
from __future__ import annotations


class ContractViolation(RuntimeError):
    def __init__(self, capability: str, operation: str, keys):
        self.capability = capability
        self.operation = operation
        self.keys = tuple(sorted(keys))
        super().__init__(f"Capability {capability} attempted undeclared {operation}: {list(self.keys)}")
