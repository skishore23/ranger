"""Validation approval must refer to the inputs and bytes actually checked."""
import copy
import json
import os
from pathlib import Path

import pytest

from core.sdk import Agent, step
from core.workspace import Workspace


def api():
    try:
        from core.evidence import capture_binding, bind_evidence, check_binding, check_evidence
    except ImportError:
        pytest.fail('Reusable evidence binding API is not implemented')
    return capture_binding, bind_evidence, check_binding, check_evidence


def test_state_binding_rejects_changed_or_missing_inputs():
    capture, bind, current, accept = api()
    original = Workspace({'candidate': {'value': 1}, 'unrelated': 0}).snapshot()
    binding = capture(original, keys=['candidate'])
    evidence = bind(binding, validator='schema@1', passed=True)
    assert accept(evidence, original, keys=['candidate'], validator='schema@1')
    changed = Workspace({'candidate': {'value': 2}}).snapshot()
    assert not accept(evidence, changed, keys=['candidate'], validator='schema@1')
    assert not accept(evidence, Workspace({}).snapshot(), keys=['candidate'], validator='schema@1')
    assert not current(binding, changed, keys=['candidate'])


def test_unrelated_state_changes_do_not_invalidate_evidence():
    capture, bind, current, accept = api()
    binding = capture({'candidate': {'b': 2, 'a': 1}, 'unrelated': 0}, keys=['candidate'])
    evidence = bind(binding, validator='schema@1', passed=True)
    assert accept(evidence, {'unrelated': 9, 'candidate': {'a': 1, 'b': 2}},
                  keys=['candidate'], validator='schema@1')


def test_exact_file_bytes_are_bound_even_with_same_size_and_timestamp(tmp_path):
    capture, bind, current, accept = api()
    artifact = tmp_path / 'candidate.bin'
    artifact.write_bytes(b'\x00old\r\n')
    binding = capture(root=tmp_path, files=['candidate.bin'])
    evidence = bind(binding, validator='format@1', passed=True)
    assert accept(evidence, root=tmp_path, files=['candidate.bin'], validator='format@1')
    stat = artifact.stat()
    artifact.write_bytes(b'\x00new\r\n')
    os.utime(artifact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert not accept(evidence, root=tmp_path, files=['candidate.bin'], validator='format@1')
    artifact.unlink()
    assert not accept(evidence, root=tmp_path, files=['candidate.bin'], validator='format@1')


def test_scope_validator_and_file_inventory_are_independently_required(tmp_path):
    capture, bind, current, accept = api()
    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / 'b.txt').write_text('b')
    args = {'root': tmp_path, 'files': ['a.txt', 'b.txt'], 'context': {'attempt': 1, 'policy': 'strict'}}
    evidence = bind(capture(**args), validator='lint@1', passed=True)
    assert accept(evidence, validator='lint@1', **args)
    assert not accept(evidence, validator='lint@2', **args)
    assert not accept(evidence, validator='lint@1', **{**args, 'files': ['a.txt']})
    assert not accept(evidence, validator='lint@1', **{**args, 'context': {'attempt': 2, 'policy': 'strict'}})
    assert not accept(evidence, validator='lint@1')


def test_binding_captured_before_validation_cannot_certify_later_bytes(tmp_path):
    capture, bind, current, accept = api()
    path = tmp_path / 'artifact.txt'
    path.write_text('checked')
    binding = capture(root=tmp_path, files=['artifact.txt'])
    path.write_text('changed during validation')
    evidence = bind(binding, validator='check@1', passed=True)
    assert not accept(evidence, root=tmp_path, files=['artifact.txt'], validator='check@1')


def test_records_are_defensive_and_json_serializable():
    capture, bind, current, accept = api()
    candidate = {'candidate': [1]}
    binding = capture(candidate, keys=['candidate'])
    details = {'observations': [1]}
    evidence = bind(binding, validator='check@1', passed=True, details=details)
    details['observations'].append(2)
    binding['state'].clear()
    assert evidence['details'] == {'observations': [1]}
    restored = json.loads(json.dumps(evidence))
    assert accept(restored, candidate, keys=['candidate'], validator='check@1')


@pytest.mark.parametrize('passed', ['true', 1, None, []])
def test_passed_must_be_an_actual_boolean(passed):
    capture, bind, current, accept = api()
    binding = capture({'candidate': 1}, keys=['candidate'])
    with pytest.raises((TypeError, ValueError)):
        bind(binding, validator='check@1', passed=passed)


@pytest.mark.parametrize('damage', ['missing', 'passed', 'binding', 'schema', 'files', 'hash'])
def test_missing_or_malformed_evidence_fails_closed(damage):
    capture, bind, current, accept = api()
    state = {'candidate': 1}
    evidence = bind(capture(state, keys=['candidate']), validator='check@1', passed=True)
    if damage == 'missing':
        evidence = None
    elif damage == 'passed':
        evidence['passed'] = 'yes'
    elif damage == 'binding':
        evidence['binding'] = []
    elif damage == 'schema':
        evidence['schema'] = 'unknown'
    elif damage == 'files':
        evidence['binding']['files'] = 'a.txt'
    else:
        evidence['binding']['state']['candidate'] = 'not-a-hash'
    check = accept(evidence, state, keys=['candidate'], validator='check@1')
    assert not check and check.unmet


def test_failure_result_is_current_but_cannot_approve():
    capture, bind, current, accept = api()
    state = {'candidate': 1}
    binding = capture(state, keys=['candidate'])
    evidence = bind(binding, validator='check@1', passed=False)
    assert current(binding, state, keys=['candidate'])
    assert not accept(evidence, state, keys=['candidate'], validator='check@1')


@pytest.mark.parametrize('args', [{}, {'keys': ['candidate']}, {'context': {'attempt': 1}}])
def test_empty_or_unavailable_subjects_cannot_be_bound(args):
    capture, bind, current, accept = api()
    with pytest.raises((ValueError, KeyError)):
        capture(**args)


@pytest.mark.parametrize('path', ['../outside.txt', 'escape.txt', 'directory'])
def test_artifact_paths_must_resolve_to_files_inside_root(tmp_path, path):
    capture, bind, current, accept = api()
    root = tmp_path / 'root'
    root.mkdir()
    (tmp_path / 'outside.txt').write_text('outside')
    (root / 'escape.txt').symlink_to(tmp_path / 'outside.txt')
    (root / 'directory').mkdir()
    with pytest.raises((ValueError, OSError)):
        capture(root=root, files=[path])


def test_registered_goal_cannot_approve_old_evidence_for_changed_state(tmp_path):
    capture, bind, current, accept = api()
    evidence = bind(capture({'candidate': 1}, keys=['candidate']), validator='check@1', passed=True)
    result = Agent([], trace_path=tmp_path / 'runs.db').run(
        initial={'candidate': 2, 'validation': copy.deepcopy(evidence)},
        goal=lambda s: accept(s.get('validation'), s, keys=['candidate'], validator='check@1'))
    assert not result.ok
    assert result.blocker.details['goal']['unmet']


def test_binding_respects_strict_read_contracts():
    capture, bind, current, accept = api()

    @step(inputs=['candidate'], outputs=['validation'], name='validate')
    def validate(state):
        binding = capture(state, keys=['hidden'])
        return {'validation': bind(binding, validator='check@1', passed=True)}

    with pytest.raises(RuntimeError, match='validate.*undeclared.*hidden'):
        Agent([validate], strict_contracts=True, trace=False).run(
            initial={'candidate': 1, 'hidden': 2}, goal=lambda s: s.exists('validation'))


def test_evidence_cannot_be_reused_in_another_root(tmp_path):
    capture, bind, current, accept = api()
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / 'artifact.txt').write_text('same bytes')
    evidence = bind(capture(root=first, files=['artifact.txt']), validator='check@1', passed=True)
    assert not accept(evidence, root=second, files=['artifact.txt'], validator='check@1')


def test_unsupported_json_values_are_not_stringified_into_bindings():
    capture, bind, current, accept = api()
    with pytest.raises(TypeError):
        capture({'candidate': object()}, keys=['candidate'])


def test_root_symlink_loop_fails_closed_on_legacy_python(tmp_path, monkeypatch):
    capture, bind, current, accept = api()
    root = tmp_path / 'root'
    root.mkdir()
    artifact = root / 'artifact.txt'
    artifact.write_text('checked')
    evidence = bind(capture(root=root, files=['artifact.txt']), validator='check@1', passed=True)
    artifact.unlink()
    root.rmdir()
    root.symlink_to(root.name, target_is_directory=True)
    resolve = Path.resolve

    def legacy_resolve(path, *args, **kwargs):
        if path == root:
            raise RuntimeError('Symlink loop from Python 3.11 Path.resolve')
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', legacy_resolve)
    check = accept(evidence, root=root, files=['artifact.txt'], validator='check@1')
    assert not check and check.unmet
