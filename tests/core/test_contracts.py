"""Capability declarations must match the state accesses they schedule."""
from dataclasses import replace

import pytest

from core.capability import Capability
from core.engine import Engine
from core.execution import selected_snapshot
from core.merge import WriteSpec
from core.plan import action, plan
from core.sdk import Agent, human, llm, step
from core.trace import replay_run


@pytest.mark.parametrize('factory', [Agent, Engine])
def test_duplicate_ids_rejected_before_execution(factory):
    @step(outputs=['done'], name='same')
    def first(state):
        pytest.fail('invalid registrations must not execute')

    with pytest.raises(ValueError, match='[Dd]uplicate.*same'):
        factory([first, replace(first, writes={'other'})])


@pytest.mark.parametrize('reader', [
    lambda s: s['hidden'], lambda s: s.get('hidden'), lambda s: s.value('hidden'),
    lambda s: s.exists('hidden'), lambda s: 'hidden' in s,
    lambda s: s.data['hidden'], lambda s: s.data.get('hidden'),
    lambda s: 'hidden' in s.data,
])
def test_strict_accessors_reject_undeclared_reads(reader, tmp_path):
    @step(inputs=['visible'], outputs=['done'], name='reader')
    def read(state):
        reader(state)
        return {'done': True}

    db = tmp_path / 'runs.db'
    with pytest.raises(RuntimeError, match='reader.*undeclared.*hidden'):
        Agent([read], strict_contracts=True, trace_path=db).run(
            initial={'visible': 1, 'hidden': 'private'}, goal=lambda s: s.exists('done'))
    recorded = replay_run(db)
    assert recorded['status'] == 'failed'
    assert 'done' not in recorded['state']


def test_strict_bulk_exports_only_include_declared_inputs():
    @step(inputs=['visible'], outputs=['done'])
    def read(state):
        exported = state.data
        assert list(exported) == ['visible']
        exported['visible'].value.append(9)
        assert state['visible'] == [1]
        assert state.get('visible') == [1]
        assert state.exists('visible') and 'visible' in state
        assert state.digest()
        return {'done': True}

    result = Agent([read], strict_contracts=True, trace=False).run(
        initial={'visible': [1], 'hidden': 2}, goal=lambda s: s.exists('done'))
    assert result.ok and result.final['visible'] == [1]
    assert result.final['hidden'] == 2


def test_read_enforcement_is_opt_in():
    @step(outputs=['done'])
    def read(state):
        return {'done': state['hidden']}

    assert Agent([read], trace=False).run(
        initial={'hidden': 3}, goal=lambda s: s.get('done') == 3).ok


def test_strict_original_snapshot_cannot_bypass_contract():
    @step(outputs=['done'], name='original')
    def read(state):
        return {'done': selected_snapshot()['hidden']}

    with pytest.raises(RuntimeError, match='original.*undeclared.*hidden'):
        Agent([read], strict_contracts=True, trace=False).run(
            initial={'hidden': 1}, goal=lambda s: s.exists('done'))


def test_strict_postcondition_cannot_read_undeclared_state():
    @step(outputs=['done'], name='post', post=lambda s, writes: s['hidden'])
    def read(state):
        return {'done': True}

    with pytest.raises(RuntimeError, match='post.*undeclared.*hidden'):
        Agent([read], strict_contracts=True, trace=False).run(
            initial={'hidden': True}, goal=lambda s: s.exists('done'))


def test_strict_llm_mapper_fails_before_provider_call():
    class Provider:
        def generate(self, **kwargs):
            pytest.fail('undeclared input must be rejected before model execution')

    @llm(outputs=['done'], provider=Provider(), map=lambda s: {'text': s['hidden']}, name='model')
    def generate(state):
        pass

    with pytest.raises(RuntimeError, match='model.*undeclared.*hidden'):
        Agent([generate], strict_contracts=True, trace=False).run(
            initial={'hidden': 1}, goal=lambda s: s.exists('done'))


def test_strict_human_callback_can_use_declared_inputs():
    @human(inputs=['visible'], outputs=['done'], title='Review')
    def review(responses, state):
        return {'done': state['visible']}

    assert Agent([review], strict_contracts=True, trace=False).run(
        initial={'visible': True}, goal=lambda s: s.get('done') is True).ok


def test_custom_runner_cannot_write_extra_keys_even_with_write_specs():
    class Runner:
        def run(self, cap, snap, context=None):
            return {'done': True, 'hidden': 2}

    cap = Capability('custom', set(), {'done'}, Runner(),
                     write_specs={'done': WriteSpec(), 'hidden': WriteSpec()})
    with pytest.raises(RuntimeError, match='custom.*undeclared.*hidden'):
        Agent([cap], trace=False).run(initial={}, goal=lambda s: s.exists('done'))


def test_plan_forwards_strict_contracts():
    @step(inputs=['visible'], outputs=['done'], name='planned')
    def read(state):
        return {'done': state['hidden']}

    compiled = plan(action(read, requires_initial=['visible'])).compile(
        strict_contracts=True, trace=False)
    with pytest.raises(RuntimeError, match='planned.*undeclared.*hidden'):
        compiled.run(initial={'visible': 1, 'hidden': 2}, goal=lambda s: s.exists('done'))


def test_registration_copies_caller_list():
    @step(outputs=['done'])
    def write(state):
        return {'done': True}

    caps = [write]
    agent = Agent(caps, trace=False)
    caps.clear()
    assert agent.run(initial={}, goal=lambda s: s.exists('done')).ok


def test_strict_presence_check_rejects_even_absent_undeclared_key():
    @step(outputs=['done'], name='probe')
    def read(state):
        return {'done': state.get('optional', True)}

    with pytest.raises(RuntimeError, match='probe.*undeclared.*optional'):
        Agent([read], strict_contracts=True, trace=False).run(
            initial={}, goal=lambda s: s.exists('done'))


def test_runtime_forwards_strict_read_contracts(tmp_path):
    from agents.common.runtime import AgentRuntime
    from topology.types import Budget

    @step(outputs=['done'], name='runtime')
    def read(state):
        return {'done': state['hidden']}

    runtime = AgentRuntime(repo_root=tmp_path, capability_list=[read],
                           budget=Budget(10000, 10000, 10), memory_key='contracts',
                           memory_domain='contracts', db_filename='contracts.db',
                           strict_contracts=True)
    with pytest.raises(RuntimeError, match='runtime.*undeclared.*hidden'):
        runtime.run_agent(initial={'hidden': 1}, goal=lambda s: s.exists('done'), max_steps=5)


def test_custom_runner_invalid_empty_result_is_not_silently_consumed():
    class Runner:
        def run(self, cap, snap, context=None):
            return []

    cap = Capability('invalid', set(), set(), Runner())
    with pytest.raises(TypeError, match='invalid.*dict or None'):
        Agent([cap], trace=False).run(initial={}, goal=lambda s: False)


@pytest.mark.parametrize('strict', [False, True])
def test_postcondition_cannot_expand_declared_writes(strict, tmp_path):
    class Runner:
        def run(self, cap, snap, context=None):
            return {'done': True}

    def postcondition(state, writes):
        writes['hidden'] = 'unexpected'
        return True

    cap = Capability('post-write', set(), {'done'}, Runner(), post=postcondition,
                     write_specs={'done': WriteSpec(), 'hidden': WriteSpec()})
    db = tmp_path / 'runs.db'
    with pytest.raises(RuntimeError, match='post-write.*undeclared.*hidden'):
        Agent([cap], strict_contracts=strict, trace_path=db).run(
            initial={}, goal=lambda s: s.exists('done'))
    recorded = replay_run(db)
    assert recorded['status'] == 'failed' and recorded['state'] == {}


def test_postcondition_original_snapshot_obeys_its_contract():
    @step(outputs=['done'], name='post-original', post=lambda s, w: selected_snapshot()['secret'])
    def write(state):
        return {'done': True}

    with pytest.raises(RuntimeError, match='post-original.*undeclared.*secret'):
        Agent([write], strict_contracts=True, trace=False).run(
            initial={'secret': True}, goal=lambda s: s.exists('done'))


def test_nested_agent_postcondition_cannot_read_outer_snapshot():
    leaks = []

    @step(outputs=['inside'], name='inner',
          post=lambda s, w: leaks.append(selected_snapshot()['secret']) or True)
    def inner(state):
        return {'inside': True}

    @step(inputs=['secret'], outputs=['done'], name='outer')
    def outer(state):
        nested = Agent([inner], strict_contracts=True, trace=False).run(
            initial={}, goal=lambda s: s.exists('inside'))
        return {'done': nested.ok}

    with pytest.raises(RuntimeError, match='inner.*undeclared.*secret'):
        Agent([outer], strict_contracts=True, trace=False).run(
            initial={'secret': True}, goal=lambda s: s.exists('done'))
    assert leaks == []


def test_original_declaration_mutation_cannot_expand_registered_outputs():
    class Runner:
        def run(self, current, state, context=None):
            cap.writes.add('hidden')
            cap.write_specs['hidden'] = WriteSpec()
            return {'done': True, 'hidden': 1}

    cap = Capability('mutable', set(), {'done'}, Runner(), write_specs={'done': WriteSpec()})
    with pytest.raises(RuntimeError, match='mutable.*undeclared.*hidden'):
        Agent([cap], trace=False).run(initial={}, goal=lambda s: s.exists('done'))


def test_runner_cannot_mutate_registered_output_contract(tmp_path):
    class Runner:
        def run(self, cap, state, context=None):
            cap.writes.add('hidden')
            cap.write_specs['hidden'] = WriteSpec()
            return {'done': True, 'hidden': 1}

    cap = Capability('immutable', set(), {'done'}, Runner(), write_specs={'done': WriteSpec()})
    db = tmp_path / 'runs.db'
    with pytest.raises(AttributeError):
        Agent([cap], trace_path=db).run(initial={}, goal=lambda s: s.exists('done'))
    assert replay_run(db)['state'] == {}
