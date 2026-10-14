"""Context-local execution limits and guarded provider boundaries.

Calls count capability attempts, not nested provider adapters. Token accounting is
an estimate: UTF-8 bytes plus framing for the complete request, and the reserved
completion limit. Reservations are never refunded, including failed requests.
Synchronous deadlines are checked before and after calls; they cannot preempt IO
or arbitrary Python code. Custom providers used directly in steps must be invoked
with ``generate_with_policy(provider, **request)``.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import json
import time
from typing import Any

from .errors import GoalBlocked
from topology.registry import list_regions
from topology.types import Atom, Budget
from topology.stitch import repair_context


@dataclass
class ExecutionState:
    budget: Budget
    started: float = field(default_factory=time.monotonic)
    calls_used: int = 0
    tokens_used: int = 0

    def check_time(self):
        if (time.monotonic() - self.started) * 1000 >= self.budget.ms:
            raise GoalBlocked('budget', {'dimension': 'ms'})

    def check_execution(self):
        self.check_time()
        for dimension, remaining in [('calls', self.budget.calls - self.calls_used),
                                     ('tokens', self.budget.tokens - self.tokens_used)]:
            if remaining <= 0:
                raise GoalBlocked('budget', {'dimension': dimension})

    def start_capability(self):
        self.check_execution()
        self.calls_used += 1

    def reserve_generation(self, request):
        self.check_time()
        # Bytes upper-bound ordinary text tokenization; include protocol framing.
        payload = {k: v for k, v in request.items() if k != 'max_tokens'}
        prompt_estimate = len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')) + 64
        remaining = self.budget.tokens - self.tokens_used - prompt_estimate
        requested = validate_max_tokens(request.get('max_tokens'))
        requested = 1500 if requested is None else requested
        if requested <= 0 or remaining <= 0:
            raise GoalBlocked('budget', {'dimension': 'tokens'})
        allowance = min(requested, remaining)
        self.tokens_used += prompt_estimate + allowance
        return allowance


_LIMITS: ContextVar[tuple] = ContextVar('ranger_generation_limits', default=())


_CURRENT: ContextVar[ExecutionState | None] = ContextVar('ranger_execution', default=None)


def current_execution() -> ExecutionState | None:
    return _CURRENT.get()


@contextmanager
def execution_scope(state: ExecutionState):
    token = _CURRENT.set(state)
    try:
        yield state
    finally:
        _CURRENT.reset(token)


@contextmanager
def generation_limits(budget=None):
    """Apply region-specific limits in addition to, never instead of, run limits."""
    if budget is None and current_execution() is not None:
        yield
        return
    if isinstance(budget, Budget):
        limits = budget
    else:
        values = budget or {}
        limits = Budget(tokens=values.get('tokens', 200000), ms=values.get('ms', 300000),
                        calls=values.get('calls', 120))
    state = ExecutionState(limits)
    state.start_capability()
    token = _LIMITS.set(_LIMITS.get() + (state,))
    try:
        yield
    finally:
        _LIMITS.reset(token)
        state.check_time()


def guard_atoms(atoms, guards=None):
    """Enforce every registered guard; never silently discard failed repair."""
    result = deepcopy(list(atoms))
    guards = list(list_regions('guard') if guards is None else guards)
    try:
        for guard in guards:
            verdict = guard.validate(result)
            if not isinstance(verdict, dict) or type(verdict.get('ok')) is not bool:
                raise ValueError('malformed guard verdict')
            if not verdict['ok']:
                result = repair_context(result, verdict, guard)
        # A later guard's repair may reintroduce a violation for an earlier one.
        for guard in guards:
            verdict = guard.validate(result)
            if not isinstance(verdict, dict) or verdict.get('ok') is not True:
                raise ValueError('guard did not validate repaired context')
    except GoalBlocked:
        raise
    except Exception as exc:
        raise GoalBlocked('policy_blocked', {'error': str(exc)}) from exc
    return result


def guard_value(value: Any) -> Any:
    atom = Atom('outbound', 'json', deepcopy(value), None, {}, {}, {})
    atoms = guard_atoms([atom])
    if len(atoms) != 1 or atoms[0].id != atom.id:
        raise GoalBlocked('policy_blocked', {'error': 'guard changed outbound envelope'})
    repaired = atoms[0].content
    if isinstance(value, dict) and not isinstance(repaired, dict):
        raise GoalBlocked('policy_blocked', {'error': 'guard destroyed structured input'})
    return repaired


def guard_request(request):
    """Protect a complete request without changing its output schema contract.

    Any schema repair is rejected, including annotation changes: distinguishing
    harmless changes from changes to extension keywords is not generally safe.
    """
    original_schema = deepcopy(request.get('schema'))
    repaired = guard_value(request)
    if repaired.get('schema') != original_schema:
        raise GoalBlocked('policy_blocked', {'error': 'guard repair changed output schema contract'})
    return repaired


def validate_max_tokens(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('max_tokens must be a non-negative integer or None')
    return value


def _generate(call, request):
    original_schema = deepcopy(request.get('schema'))
    request = guard_request(request)
    requested = validate_max_tokens(request.get('max_tokens'))
    if requested is not None and requested <= 0:
        raise GoalBlocked('budget', {'dimension': 'tokens'})
    states = list(_LIMITS.get())
    state = current_execution()
    if state is not None:
        states.append(state)
    # First find the tightest ceiling, then reserve the same request everywhere.
    for state in states:
        used = state.tokens_used
        request['max_tokens'] = state.reserve_generation(request)
        state.tokens_used = used
    for state in states:
        state.reserve_generation(request)
    try:
        response = call(**request)
    finally:
        for state in states:
            state.check_time()
    if original_schema is not None:
        validate_response(response, original_schema)
    return response


def validate_response(response, schema):
    from jsonschema import validate, ValidationError, SchemaError
    try:
        parsed = json.loads(response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc
    try:
        validate(parsed, schema)
    except (ValidationError, SchemaError) as exc:
        raise RuntimeError(f"LLM returned schema-invalid JSON: {exc.message}") from exc
    return parsed


def protected_generation(fn):
    """Mark a built-in leaf provider as enforcing its own outbound boundary."""
    @wraps(fn)
    def wrapped(self, **request):
        return _generate(lambda **checked: fn(self, **checked), request)
    wrapped.__ranger_protected__ = True
    return wrapped


def generate_with_policy(provider, **request):
    """Invoke built-in or injected providers with run-local limits and guards."""
    if getattr(provider.generate, '__ranger_protected__', False) is True:
        return provider.generate(**request)
    return _generate(provider.generate, request)
