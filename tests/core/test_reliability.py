"""Regression coverage for run isolation and execution boundaries (offline)."""
import json
import time
from types import SimpleNamespace

import pytest

from core.engine import Engine
from core.errors import GoalBlocked
from core.llm.provider import OpenAIProvider
from core.merge import MergeMode, WriteSpec
from core.sdk import Agent, llm, step, tool
from core.workspace import Workspace
from regions.guard_pii import GuardPII
from regions.llm_openai import LLMOpenAI
from topology.registry import clear_registry, register_region
from topology.types import Atom, Budget


@pytest.fixture(autouse=True)
def isolated_registry():
    clear_registry()
    yield
    clear_registry()


class FakeProvider:
    def __init__(self, response='ok'):
        self.requests = []
        self.response = response

    def generate(self, **request):
        self.requests.append(request)
        return self.response


def test_nested_workspace_ownership():
    initial = {'nested': {'items': [1]}}
    ws = Workspace(initial)
    old = ws.snapshot()
    initial['nested']['items'].append(9)
    old.get('nested')['items'].append(2)
    old.value('nested')['items'].append(3)
    old.data['nested'].value['items'].append(4)
    assert old.get('nested') == {'items': [1]}
    assert ws.snapshot().get('nested') == {'items': [1]}
    written = {'items': [5]}
    ws.cas_commit({'nested': written}, provenance_id='test')
    written['items'].append(6)
    assert ws.snapshot()['nested'] == {'items': [5]}
    assert 'nested' in old
    assert old.get('nested') == {'items': [1]}


def test_commit_validates_atomically():
    ws = Workspace()
    def reject(value):
        raise ValueError('rejected')
    with pytest.raises(ValueError):
        ws.cas_commit({'first': 1, 'second': 1}, write_specs={
            'first': WriteSpec(), 'second': WriteSpec(validator=reject)}, provenance_id='test')
    assert not ws.snapshot().exists('first')


def test_validation_receives_merged_value():
    ws = Workspace({'value': {'a': 1}})
    def both(value):
        assert value == {'a': 1, 'b': 2}
    ws.cas_commit({'value': {'b': 2}}, write_specs={
        'value': WriteSpec(merge_mode=MergeMode.MERGE_JSON, validator=both)}, provenance_id='test')


def test_repeated_agent_runs_and_final_batch():
    @step(inputs=['x'], outputs=['y'])
    def produce(s):
        return {'y': s.get('x') + 1}
    agent = Agent([produce])
    for _ in range(2):
        result = agent.run(initial={'x': 1, 'y': 0}, goal=lambda s: s.get('y') == 2, max_steps=1)
        assert result.ok
        assert result.steps == 1


def test_initial_goal_with_no_steps():
    assert Agent([]).run(initial={}, goal=lambda s: True, max_steps=0).ok


def test_refresh_soft_failure_is_retried():
    attempts = []
    @step(inputs=['x'], outputs=['y'])
    def refresh(s):
        attempts.append(1)
        if len(attempts) == 1:
            raise GoalBlocked('llm_invalid_output')
        return {'y': 2}
    result = Agent([refresh]).run(initial={'x': 1, 'y': 0}, goal=lambda s: s.get('y') == 2)
    assert result.ok
    assert len(attempts) == 2


def test_batches_exclude_both_read_write_hazards():
    @step(inputs=['x'], outputs=['y'])
    def a(s):
        return {'y': s.get('x')}
    @step(inputs=['y'], outputs=['z'])
    def b(s):
        return {'z': s.get('y')}
    engine = Engine([a, b])
    assert len(engine._pick_compatible([a, b])) == 1
    assert len(engine._pick_compatible([b, a])) == 1


def test_batch_runners_read_selected_snapshot():
    @step(outputs=['a'])
    def first(s):
        return {'a': 1}
    @step(outputs=['b'])
    def second(s):
        return {'b': s.get('a', 0)}
    result = Agent([first, second]).run(initial={}, goal=lambda s: s.exists('b'))
    assert result.final.get('b') == 0


@pytest.mark.parametrize('dimension', ['calls', 'ms', 'tokens'])
def test_zero_budget_prevents_execution(dimension):
    called = []
    @step(outputs=['done'])
    def run(s):
        called.append(1)
        return {'done': True}
    values = dict(tokens=1000, ms=1000, calls=10)
    values[dimension] = 0
    result = Agent([run], budget=Budget(**values)).run(initial={}, goal=lambda s: s.exists('done'))
    assert not result.ok
    assert result.blocker.reason == 'budget'
    assert not called


def test_calls_shared_across_batch():
    @step(outputs=['a'])
    def first(s):
        return {'a': 1}
    @step(outputs=['b'])
    def second(s):
        return {'b': 1}
    result = Agent([first, second], budget=Budget(1000, 1000, 1)).run(initial={}, goal=lambda s: s.exists('b'))
    assert not result.ok
    assert result.final.exists('a')
    assert not result.final.exists('b')


def test_elapsed_time_checked_after_operation():
    @step(outputs=['done'])
    def slow(s):
        time.sleep(.02)
        return {'done': True}
    result = Agent([slow], budget=Budget(1000, 1, 5)).run(initial={}, goal=lambda s: s.exists('done'))
    assert not result.ok
    assert result.blocker.reason == 'budget'


def test_shared_model_allowance_and_clamp():
    fake = FakeProvider('x' * 20)
    @llm(outputs=['a'], provider=fake, template='hello', max_tokens=1000)
    def first(s): pass
    @llm(inputs=['a'], outputs=['b'], provider=fake, template='hello', max_tokens=1000)
    def second(s): pass
    result = Agent([first, second], budget=Budget(180, 1000, 10)).run(initial={}, goal=lambda s: s.exists('b'))
    assert fake.requests
    assert fake.requests[0]['max_tokens'] < 180
    assert len(fake.requests) == 1
    assert not result.ok


@pytest.mark.parametrize('mode', ['exception', 'block', 'broken_redaction'])
def test_guards_fail_closed(mode):
    fake = FakeProvider()
    guard = GuardPII('guard', 'block' if mode == 'block' else 'mask')
    if mode == 'exception':
        def fail(atoms): raise ValueError('guard failed')
        guard.validate = fail
    if mode == 'broken_redaction':
        def fail(*args): raise ValueError('repair failed')
        guard.redact_atoms = fail
    register_region(guard)
    @llm(outputs=['out'], provider=fake, system='secret@example.com', template='hello')
    def invoke(s): pass
    result = Agent([invoke]).run(initial={}, goal=lambda s: s.exists('out'))
    assert not result.ok
    assert not fake.requests


def test_mask_entire_model_request_and_structured_actions():
    register_region(GuardPII('guard'))
    fake = FakeProvider()
    @llm(outputs=['out'], provider=fake, system='secret@example.com', template='{{ source }}',
         map=lambda s: {'source': 'map@example.com'})
    def invoke(s): pass
    assert Agent([invoke]).run(initial={}, goal=lambda s: s.exists('out')).ok
    assert '@example.com' not in json.dumps(fake.requests)
    received = []
    @tool(inputs=['nested'], outputs=['done'])
    def action(s):
        received.append(s.get('nested'))
        return {'done': True}
    assert Agent([action]).run(initial={'nested': {'email': 'user@example.com'}}, goal=lambda s: s.exists('done')).ok
    assert received == [{'email': '[EMAIL_REDACTED]'}]


def test_guarded_summary_never_uses_unrepaired_memory():
    register_region(GuardPII('guard'))
    seen = []
    class Memory:
        kind = 'memory'
        key = 'memory'
        def read(self, query):
            return [Atom('a', 'json', {'email': 'user@example.com'}, None, {'source': self.key}, {}, {})]
        def summarize(self, atoms, goal):
            seen.extend(a.content for a in atoms)
            return Atom('s', 'text', 'summary@example.com', None, {'source': self.key}, {}, {})
    register_region(Memory())
    fake = FakeProvider()
    @llm(outputs=['out'], provider=fake, template='{{ context_window }}')
    def invoke(s): pass
    result = Agent([invoke]).run(initial={}, goal=lambda s: s.exists('out'))
    assert result.ok
    assert seen == [{'email': '[EMAIL_REDACTED]'}]
    assert '@example.com' not in json.dumps(fake.requests)


def test_structured_output_schema_rejected():
    fake = FakeProvider('{"count": "bad"}')
    @llm(outputs=['out'], provider=fake, schema={'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']})
    def invoke(s): pass
    with pytest.raises((RuntimeError, ValueError)):
        Agent([invoke]).run(initial={}, goal=lambda s: s.exists('out'))


def test_direct_provider_in_step_guarded_and_zero_temperature():
    requests = []
    provider = object.__new__(OpenAIProvider)
    def create(**request):
        requests.append(request)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))])
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    register_region(GuardPII('guard'))
    @step(outputs=['done'])
    def action(s):
        return {'done': provider.generate(prompt='user@example.com', model='fake', temperature=0)}
    result = Agent([action]).run(initial={}, goal=lambda s: s.exists('done'))
    assert result.ok
    assert requests[0]['temperature'] == 0
    assert '@example.com' not in json.dumps(requests)


@pytest.mark.parametrize('budget', [Budget(0, 1000, 2), Budget(1000, 0, 2), Budget(1000, 1000, 0), {'max_tokens': 0}])
def test_standalone_region_zero_budget(budget):
    region = object.__new__(LLMOpenAI)
    region.key, region.model, region.system_prompt = 'model', 'fake', 'system'
    region.max_tokens, region.temperature = 1500, .2
    region._provider = FakeProvider()
    with pytest.raises(GoalBlocked):
        list(region.infer({'text': 'hello'}, [], budget=budget))
    assert not region._provider.requests


@pytest.mark.parametrize('dimension', ['tokens', 'ms', 'calls'])
@pytest.mark.parametrize('value', [-1, 1.5, True, '5', float('inf')])
def test_invalid_budget_values_rejected(dimension, value):
    values = dict(tokens=1000, ms=1000, calls=2)
    values[dimension] = value
    with pytest.raises(ValueError, match=dimension):
        Budget(**values)


@pytest.mark.parametrize('value', [-1, 1.5, True, '5', float('inf')])
def test_invalid_generation_limit_rejected(value):
    from core.execution import generate_with_policy
    fake = FakeProvider()
    with pytest.raises(ValueError, match='max_tokens'):
        generate_with_policy(fake, prompt='hello', model='fake', max_tokens=value)
    assert not fake.requests


def test_region_adapter_explicit_zero_temperature_overrides_defaults():
    from core.llm.provider import RegionBackedProvider
    requests = []
    class Region:
        key, kind = 'fake', 'model'
        def infer(self, prompt, window, budget):
            requests.append(prompt)
            yield Atom('out', 'text', 'ok', None, {}, {}, {})
    register_region(Region())
    provider = RegionBackedProvider('fake', default_options={'temperature': .8})
    assert provider.generate(prompt='hello', model='fake', temperature=0) == 'ok'
    assert requests[0]['options']['temperature'] == 0


def test_custom_provider_calls_in_step_share_limits_and_cleanup():
    from core.execution import generate_with_policy, current_execution
    fake = FakeProvider('ok')
    states = []
    @step(outputs=['out'])
    def invoke(s):
        states.append(current_execution())
        for _ in range(2):
            generate_with_policy(fake, prompt='hello', model='fake', max_tokens=1000)
        return {'out': True}
    agent = Agent([invoke], budget=Budget(250, 1000, 1))
    for _ in range(2):
        result = agent.run(initial={}, goal=lambda s: s.exists('out'))
        assert not result.ok
        assert result.blocker.reason == 'budget'
        assert current_execution() is None
    assert len(fake.requests) == 2
    assert states[0] is not states[1]
    assert all(state.calls_used == 1 for state in states)


def test_standalone_region_clamps_options_and_checks_late_return():
    region = object.__new__(LLMOpenAI)
    region.key, region.model, region.system_prompt = 'model', 'fake', 'system'
    region.max_tokens, region.temperature = 1500, .2
    region._provider = FakeProvider()
    list(region.infer({'text': 'hello', 'options': {'max_tokens': 100}}, [], budget={'max_tokens': 10}))
    assert region._provider.requests[0]['max_tokens'] == 10
    class Slow(FakeProvider):
        def generate(self, **request):
            time.sleep(.02)
            return super().generate(**request)
    region._provider = Slow()
    with pytest.raises(GoalBlocked, match='budget'):
        list(region.infer({'text': 'hello'}, [], budget=Budget(10000, 1, 1)))


def test_all_guards_apply_even_above_planner_limit():
    fake = FakeProvider()
    for index in range(4):
        guard = GuardPII(f'guard{index}')
        if index == 3:
            guard.validate = lambda atoms: {'ok': False}
        register_region(guard)
    @llm(outputs=['out'], provider=fake)
    def invoke(s): pass
    result = Agent([invoke]).run(initial={}, goal=lambda s: s.exists('out'))
    assert not result.ok
    assert not fake.requests


def test_failed_post_refresh_remains_pending_with_other_progress():
    attempts = []
    def verify(s, writes):
        attempts.append(1)
        if len(attempts) < 2:
            raise GoalBlocked('llm_invalid_output')
        return True
    @step(inputs=['x'], outputs=['y'], post=verify)
    def refresh(s):
        return {'y': 2}
    @step(outputs=['other'])
    def progress(s):
        return {'other': True}
    result = Agent([refresh, progress]).run(initial={'x': 1, 'y': 0}, goal=lambda s: s.get('y') == 2)
    assert result.ok and len(attempts) == 2


def test_region_adapters_reserve_once_and_respect_shared_run_budget():
    from core.llm.provider import RegionBackedProvider
    from core.execution import current_execution
    region = object.__new__(LLMOpenAI)
    region.key, region.model, region.system_prompt = 'model', 'fake', 'system'
    region.max_tokens, region.temperature = 50, .2
    region._provider = FakeProvider()
    register_region(region)
    captured = []
    @step(outputs=['out'])
    def invoke(s):
        provider = RegionBackedProvider('model', budget={'max_tokens': 10})
        result = provider.generate(prompt='hello', model='fake')
        captured.append(current_execution().tokens_used)
        return {'out': result}
    result = Agent([invoke], budget=Budget(400, 1000, 1)).run(initial={}, goal=lambda s: s.exists('out'))
    assert result.ok
    assert len(region._provider.requests) == 1
    request = region._provider.requests[0]
    expected = len(json.dumps({k: v for k, v in request.items() if k != 'max_tokens'}, ensure_ascii=False, sort_keys=True).encode('utf-8')) + 64 + 10
    assert captured == [expected]
