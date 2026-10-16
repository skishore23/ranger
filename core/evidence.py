"""Content bindings for reusable, JSON-serializable validation evidence.

Capture before running a validator, attach its actual Boolean outcome, and check
against independently supplied current dependencies at acceptance. These records
are local data, not authenticated attestations or filesystem locks.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .errors import GoalCheck
from .serialization import dumps, fingerprint
from .state import Snapshot


_BINDING = 'ranger.binding@1'
_EVIDENCE = 'ranger.evidence@1'
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def _names(values, label):
    if isinstance(values, (str, bytes, Path)):
        raise TypeError(f'{label} must be a collection, not a single name')
    names = list(values)
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        raise ValueError(f'{label} must contain unique, non-empty strings')
    return sorted(names)


def _relative_path(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or re.match(r'^[A-Za-z]:', name):
        raise ValueError('Artifact paths must be relative and remain inside root')
    return path


def capture_binding(state=None, *, keys=(), root=None, files=(), context=None) -> dict:
    """Fingerprint declared JSON values and exact file bytes before validation.

    State may be a Snapshot or mapping. Files are root-relative names; canonical
    resolved paths and the absolute root are included. Context binds the attempt,
    validation settings, or other caller-defined scope. At least one key or file
    is required. No undeclared dependency is inferred.
    """
    keys = _names(keys, 'keys')
    files = _names(files, 'files')
    if not keys and not files:
        raise ValueError('A binding requires at least one state key or file')
    if keys and not isinstance(state, (Snapshot, Mapping)):
        raise ValueError('State is required for declared keys')
    fingerprints = {}
    for key in keys:
        if key not in state:
            raise KeyError(f'Missing binding input: {key}')
        fingerprints[key] = fingerprint(state[key])
    root_path = None
    if root is not None:
        if isinstance(root, str) and not root:
            raise ValueError('Artifact root must not be empty')
        try:
            root_path = Path(root).resolve(strict=True)
        except RuntimeError as exc:
            raise ValueError('Cannot resolve artifact root') from exc
        if not root_path.is_dir():
            raise ValueError('Artifact root must be a directory')
    if files and root_path is None:
        raise ValueError('Root is required for file artifacts')
    artifacts = []
    paths = set()
    for name in files:
        try:
            target = (root_path / _relative_path(name)).resolve(strict=True)
        except RuntimeError as exc:
            raise ValueError('Cannot resolve artifact path') from exc
        if not target.is_relative_to(root_path) or not target.is_file():
            raise ValueError('Artifact must be a file inside root')
        relative = target.relative_to(root_path).as_posix()
        if relative in paths:
            raise ValueError('Artifact aliases must not duplicate a canonical file')
        paths.add(relative)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        artifacts.append({'path': relative, 'sha256': digest})
    return {'schema': _BINDING, 'state': fingerprints,
            'root': str(root_path) if root_path is not None else None,
            'files': sorted(artifacts, key=lambda item: item['path']),
            'context': fingerprint(context) if context is not None else None}


def _is_hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _validate_binding(binding):
    dumps(binding)
    if (not isinstance(binding, dict) or set(binding) != {'schema', 'state', 'root', 'files', 'context'}
            or binding.get('schema') != _BINDING or not isinstance(binding.get('state'), dict)
            or not isinstance(binding.get('files'), list)):
        raise ValueError('Malformed evidence binding')
    for key, digest in binding['state'].items():
        if not key or not _is_hash(digest):
            raise ValueError('Malformed bound state fingerprint')
    root = binding['root']
    if root is not None and (not isinstance(root, str) or not root or not Path(root).is_absolute()):
        raise ValueError('Malformed artifact root')
    if binding['context'] is not None and not _is_hash(binding['context']):
        raise ValueError('Malformed validation context')
    paths = []
    for artifact in binding['files']:
        if (not isinstance(artifact, dict) or set(artifact) != {'path', 'sha256'}
                or not isinstance(artifact.get('path'), str) or not _is_hash(artifact.get('sha256'))):
            raise ValueError('Malformed bound artifact')
        path = artifact['path']
        if _relative_path(path).as_posix() != path:
            raise ValueError('Artifact path must be canonical')
        paths.append(path)
    if paths != sorted(set(paths)) or (paths and root is None) or not (paths or binding['state']):
        raise ValueError('Invalid binding dependencies')


def bind_evidence(binding, *, validator: str, passed: bool, details=None) -> dict:
    """Attach an actual validator outcome to its original pre-validation binding."""
    _validate_binding(binding)
    if not isinstance(validator, str) or not validator.strip():
        raise ValueError('Validator identifier must be a non-empty string')
    if type(passed) is not bool:
        raise TypeError('Validation passed must be a bool')
    dumps(details)
    return {'schema': _EVIDENCE, 'validator': validator, 'passed': passed,
            'binding': deepcopy(binding), 'details': deepcopy(details)}


def check_binding(binding, state=None, *, keys=(), root=None, files=(), context=None) -> GoalCheck:
    """Fail closed unless a binding matches independently declared current inputs."""
    try:
        _validate_binding(binding)
    except (TypeError, ValueError, KeyError):
        return GoalCheck(False, ('Validation binding is missing or malformed',))
    try:
        current = capture_binding(state, keys=keys, root=root, files=files, context=context)
    except (OSError, TypeError, ValueError, KeyError):
        return GoalCheck(False, ('Current validation inputs are unavailable or invalid',))
    if binding != current:
        return GoalCheck(False, ('Validation inputs changed',))
    return GoalCheck(True)


def check_evidence(evidence, state=None, *, validator: str, keys=(), root=None, files=(), context=None) -> GoalCheck:
    """Require well-formed passing evidence from the expected validator and scope.

    Current keys/files/context must come from the application's current request,
    never be copied from an untrusted evidence record to define its own scope.
    """
    try:
        dumps(evidence)
        if (not isinstance(evidence, dict)
                or set(evidence) != {'schema', 'validator', 'passed', 'binding', 'details'}
                or evidence.get('schema') != _EVIDENCE or type(evidence.get('passed')) is not bool
                or not isinstance(evidence.get('validator'), str) or not evidence['validator'].strip()):
            raise ValueError('Malformed validation evidence')
        _validate_binding(evidence['binding'])
    except (TypeError, ValueError, KeyError):
        return GoalCheck(False, ('Validation evidence is missing or malformed',))
    if evidence['validator'] != validator:
        return GoalCheck(False, ('Validation came from a different validator',))
    if not evidence['passed']:
        return GoalCheck(False, ('Validation did not pass',))
    return check_binding(evidence['binding'], state, keys=keys, root=root, files=files, context=context)
