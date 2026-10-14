"""A shared state policy applies to seeds and every published update."""
import json

import pytest

from core.capability import Capability
from core.engine import Engine
from core.merge import MergeMode, WriteSpec
from core.plan import action, plan
from core.sdk import Agent, llm, step, tool
from core.trace import replay_run
from core.workspace import Workspace


def api():
    try:
        from core.sdk import FieldContract, FieldContractViolation
    except ImportError:
        pytest.fail('Shared field contract API is not implemented')
    return FieldContract, FieldContractViolation


@pytest.mark.parametrize('value', ['invalid', True, None])
@pytest.mark.parametrize('factory', [Agent, Engine])
def test_invalid_initial_value_rejected_before_goal_or_runner(value, factory):
    Field, Violation = api()
    agent = factory([], trace=False, field_contracts={
        'count': Field(schema={'type': 'integer'}, allow_initial=True)})
    run = agent.run if factory is Agent else agent.solve
    with pytest.raises(Violation) as failure:
        run(initial={'count': value}, goal=lambda s: pytest.fail('invalid state reached goal'))
    assert failure.value.field == 'count'
    assert failure.value.reason == 'schema'


def test_unknown_or_forbidden_seed_cannot_satisfy_initial_goal():
    Field, Violation = api()
    agent = Agent([], trace=False, field_contracts={
        'approval': Field(schema={'type': 'boolean'}, allowed_writers=['init'])})
    for initial, reason in [({'other': 1}, 'unknown_field'), ({'approval': True}, 'initial_not_allowed')]:
        with pytest.raises(Violation) as failure:
            agent.run(initial=initial, goal=lambda s: True, max_steps=0)
        assert failure.value.reason == reason


def test_valid_seed_can_satisfy_goal_without_capability_execution():
    Field, _ = api()
    contract = Field(schema={'type': 'integer', 'minimum': 0}, allow_initial=True)
    assert Agent([], field_contracts={'count': contract}, trace=False).run(
        initial={'count': 2}, goal=lambda s: s['count'] == 2, max_steps=0).ok


@pytest.mark.parametrize('contracts', [None, {}])
def test_omitted_contracts_keep_legacy_behavior_but_empty_map_is_closed(contracts):
    _, Violation = api()
    agent = Agent([], trace=False, field_contracts=contracts)
    if contracts is None:
        assert agent.run(initial={'anything': object()}, goal=lambda s: True).ok
    else:
        with pytest.raises(Violation, match='anything'):
            agent.run(initial={'anything': 1}, goal=lambda s: True)


@pytest.mark.parametrize('wrong', ['unknown', 'unauthorized'])
def test_registration_rejects_unknown_fields_and_disallowed_writers(wrong):
    Field, Violation = api()
    @step(outputs=['approval'], name='model')
    def model(state):
        pytest.fail('disallowed writer must never execute')
    fields = {} if wrong == 'unknown' else {
        'approval': Field(schema={'type': 'boolean'}, allowed_writers=['reviewer'])}
    with pytest.raises(Violation) as failure:
        Agent([model], field_contracts=fields)
    assert failure.value.field == 'approval'
    assert failure.value.writer == 'model'
    assert failure.value.reason == ('unknown_field' if wrong == 'unknown' else 'writer_not_allowed')


def test_unknown_declared_input_rejected():
    _, Violation = api()
    @step(inputs=['missing'], name='reader')
    def reader(state):
        return {}
    with pytest.raises(Violation, match='missing'):
        Agent([reader], field_contracts={})


@pytest.mark.parametrize('kind', ['step', 'tool', 'llm', 'custom', 'post'])
def test_all_runners_and_postconditions_share_value_validation(kind, tmp_path):
    Field, Violation = api()
    if kind == 'llm':
        class Provider:
            def generate(self, **kwargs):
                return json.dumps({'value': 'invalid'})
        @llm(outputs=['count'], name='writer', provider=Provider())
        def writer(state):
            pass
    elif kind == 'custom':
        class Runner:
            def run(self, cap, state, context=None):
                return {'count': 'invalid'}
        writer = Capability('writer', set(), {'count'}, Runner(), write_specs={'count': WriteSpec()})
    else:
        decorator = tool if kind == 'tool' else step
        post = (lambda s, writes: writes.update(count='invalid') or True) if kind == 'post' else None
        @decorator(outputs=['count'], name='writer', post=post)
        def writer(state):
            return {'count': 1 if kind == 'post' else 'invalid'}
    db = tmp_path / 'runs.db'
    agent = Agent([writer], field_contracts={
        'count': Field(schema={'type': 'integer'}, allowed_writers=['writer'])}, trace_path=db)
    with pytest.raises(Violation):
        agent.run(initial={}, goal=lambda s: s.exists('count'))
    recorded = replay_run(db)
    assert recorded['status'] == 'failed' and recorded['state'] == {}


def test_workspace_authority_is_explicit_not_parsed_from_provenance():
    Field, Violation = api()
    ws = Workspace(field_contracts={
        'approval': Field(schema={'type': 'boolean'}, allowed_writers=['reviewer'])})
    for writer in (None, 'model'):
        with pytest.raises(Violation) as failure:
            ws.cas_commit({'approval': True}, provenance_id='reviewer:fake', writer=writer)
        assert failure.value.reason == 'writer_not_allowed'
    ws.cas_commit({'approval': True}, provenance_id='anything', writer='reviewer')
    assert ws.snapshot()['approval'] is True


def test_shared_validation_is_atomic_and_checks_merged_values():
    Field, Violation = api()
    ws = Workspace({'record': {'a': 1}}, field_contracts={
        'record': Field(schema={'type': 'object', 'required': ['a'], 'additionalProperties': {'type': 'integer'}},
                        allowed_writers=['writer'], allow_initial=True),
        'other': Field(schema={'type': 'boolean'}, allowed_writers=['writer']),
    })
    ws.cas_commit({'record': {'b': 2}}, provenance_id='ok', writer='writer',
                  write_specs={'record': WriteSpec(merge_mode=MergeMode.MERGE_JSON)})
    assert ws.snapshot()['record'] == {'a': 1, 'b': 2}
    before = ws.snapshot()
    with pytest.raises(Violation):
        ws.cas_commit({'other': True, 'record': {'b': 'bad'}}, provenance_id='bad', writer='writer',
                      write_specs={'record': WriteSpec(merge_mode=MergeMode.MERGE_JSON), 'other': WriteSpec()})
    assert ws.snapshot().digest() == before.digest()
    assert not ws.snapshot().exists('other')


def test_legacy_write_validator_cannot_weaken_shared_schema():
    Field, Violation = api()
    @step(outputs=['count'], name='writer', write_specs={'count': WriteSpec(validator=lambda value: None)})
    def writer(state):
        return {'count': 'bad'}
    with pytest.raises(Violation):
        Agent([writer], trace=False, field_contracts={
            'count': Field(schema={'type': 'integer'}, allowed_writers=['writer'])}).run(
                initial={}, goal=lambda s: s.exists('count'))


def test_contracts_are_defensively_captured_and_repeated_runs_are_checked():
    Field, Violation = api()
    schema, writers = {'type': 'integer'}, ['writer']
    contract = Field(schema=schema, allowed_writers=writers, allow_initial=True)
    fields = {'count': contract}
    @step(inputs=['count'], outputs=['count'], name='writer')
    def writer(state):
        return {'count': state['count'] + 1}
    agent = Agent([writer], trace=False, field_contracts=fields)
    fields.clear()
    schema['type'] = 'string'
    writers.append('intruder')
    contract.schema['type'] = 'string'
    assert agent.run(initial={'count': 1}, goal=lambda s: s['count'] == 2).ok
    with pytest.raises(Violation):
        agent.run(initial={'count': 'bad'}, goal=lambda s: True)


def test_guard_repair_cannot_feed_invalid_field_values_to_tools(tmp_path):
    Field, Violation = api()
    from unittest.mock import patch
    @tool(inputs=['count'], outputs=['done'], name='writer')
    def writer(state):
        pytest.fail('invalid repaired inputs reached tool')
    fields = {'count': Field(schema={'type': 'integer'}, allow_initial=True),
              'done': Field(schema={'type': 'boolean'}, allowed_writers=['writer'])}
    with patch('core.engine.guard_value', return_value={'count': 'bad'}), pytest.raises(Violation):
        Agent([writer], field_contracts=fields, trace_path=tmp_path / 'runs.db').run(
            initial={'count': 1}, goal=lambda s: s.exists('done'))


@pytest.mark.parametrize('value', [float('nan'), ('tuple',), object()])
def test_field_values_are_strict_json_even_without_recording(value):
    Field, Violation = api()
    with pytest.raises(Violation):
        Workspace({'value': value}, field_contracts={'value': Field(schema=True, allow_initial=True)})


@pytest.mark.parametrize('trace', [False, True])
def test_non_json_update_uses_shared_error_with_or_without_recording(tmp_path, trace):
    Field, Violation = api()
    @step(outputs=['value'], name='writer')
    def writer(state):
        return {'value': float('nan')}
    agent = Agent([writer], trace=trace, trace_path=tmp_path / 'runs.db',
                  field_contracts={'value': Field(schema=True, allowed_writers=['writer'])})
    with pytest.raises(Violation) as failure:
        agent.run(initial={}, goal=lambda s: s.exists('value'))
    assert failure.value.reason == 'schema'


@pytest.mark.parametrize('kind', ['cycle', 'mixed_keys'])
@pytest.mark.parametrize('trace', [False, True])
def test_provenance_hashing_cannot_preempt_shared_value_errors(tmp_path, kind, trace):
    Field, Violation = api()
    value = []
    value.append(value)
    if kind == 'mixed_keys':
        value = {'name': 'valid', 1: 'invalid key'}
    @step(outputs=['value'], name='writer')
    def writer(state):
        return {'value': value}
    agent = Agent([writer], trace=trace, trace_path=tmp_path / 'runs.db',
                  field_contracts={'value': Field(schema=True, allowed_writers=['writer'])})
    with pytest.raises(Violation) as failure:
        agent.run(initial={}, goal=lambda s: s.exists('value'))
    assert failure.value.field == 'value' and failure.value.reason == 'schema'


@pytest.mark.parametrize('options', [
    {'schema': {'type': 'imaginary'}}, {'schema': {'type': 'integer'}, 'allowed_writers': 'writer'},
    {'schema': True, 'allowed_writers': ['']}, {'schema': True, 'allow_initial': 'yes'},
])
def test_malformed_contract_configuration_rejected(options):
    Field, _ = api()
    with pytest.raises((TypeError, ValueError)):
        Field(**options)


def test_local_schema_references_and_nested_constraints():
    Field, Violation = api()
    schema = {'$defs': {'positive': {'type': 'integer', 'minimum': 1}},
              'type': 'object', 'required': ['amount'], 'additionalProperties': False,
              'properties': {'amount': {'$ref': '#/$defs/positive'}}}
    field = Field(schema=schema, allow_initial=True)
    assert Workspace({'record': {'amount': 2}}, field_contracts={'record': field}).snapshot()['record']['amount'] == 2
    with pytest.raises(Violation) as failure:
        Workspace({'record': {'amount': 0}}, field_contracts={'record': field})
    assert failure.value.path == ('amount',)


def test_external_schema_references_fail_without_network(monkeypatch):
    Field, Violation = api()
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('validation fetched a remote schema'))
    field = Field(schema={'$ref': 'https://example.org/schema.json'}, allow_initial=True)
    with pytest.raises(Violation):
        Workspace({'value': 1}, field_contracts={'value': field})


def test_error_does_not_include_invalid_value():
    Field, Violation = api()
    secret = 'sensitive-raw-input'
    with pytest.raises(Violation) as failure:
        Workspace({'count': secret}, field_contracts={
            'count': Field(schema={'type': 'integer'}, allow_initial=True)})
    assert secret not in str(failure.value)


@pytest.mark.parametrize('initial', [[], '', False, 0])
def test_malformed_initial_container_is_not_silently_empty(initial):
    api()
    with pytest.raises(TypeError, match='[Ii]nitial.*mapping'):
        Workspace(initial, field_contracts={})


def test_disjoint_draft_and_approval_fields_have_separate_authorities():
    Field, Violation = api()
    @step(outputs=['draft'], name='model')
    def model(state):
        return {'draft': {'text': 'candidate'}}
    @step(inputs=['draft'], outputs=['approval'], name='reviewer')
    def reviewer(state):
        return {'approval': state['draft']['text'] == 'candidate'}
    fields = {'draft': Field(schema={'type': 'object', 'required': ['text'], 'additionalProperties': False,
                                    'properties': {'text': {'type': 'string'}}}, allowed_writers=['model']),
              'approval': Field(schema={'type': 'boolean'}, allowed_writers=['reviewer'])}
    assert Agent([model, reviewer], trace=False, field_contracts=fields, strict_contracts=True).run(
        initial={}, goal=lambda s: s.get('approval') is True).ok
    with pytest.raises(Violation, match='approval'):
        Agent([model, reviewer], trace=False, field_contracts=fields).run(
            initial={'approval': True}, goal=lambda s: True)


def test_plan_and_runtime_forward_shared_contracts(tmp_path):
    Field, Violation = api()
    from agents.common.runtime import AgentRuntime
    from topology.types import Budget
    @step(inputs=['count'], outputs=['done'], name='writer')
    def writer(state):
        return {'done': state['count'] == 1}
    fields = {'count': Field(schema={'type': 'integer'}, allow_initial=True),
              'done': Field(schema={'type': 'boolean'}, allowed_writers=['writer']),
              'repo.root': Field(schema={'type': 'string'}, allow_initial=True)}
    compiled = plan(action(writer, requires_initial=['count'])).compile(field_contracts=fields)
    assert compiled.run(initial={'count': 1}, goal=lambda s: s.get('done')).ok
    runtime = AgentRuntime(repo_root=tmp_path, capability_list=[writer], budget=Budget(10000, 10000, 10),
                           memory_key='fields', memory_domain='fields', db_filename='fields.db', field_contracts=fields)
    assert runtime.run_agent(initial={'count': 1}, goal=lambda s: s.get('done'), max_steps=5).ok
    with pytest.raises(Violation):
        runtime.run_agent(initial={'count': 'bad'}, goal=lambda s: True, max_steps=0)
