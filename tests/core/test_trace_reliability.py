import json
import sqlite3
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from core.sdk import Agent, step, tool
from core.errors import GoalBlocked
from topology.types import Atom, Budget
from topology.attest import sign_atom, verify_atom, verify_atom_chain, create_provenance_chain
from regions.mem_sqlite import MemSQLite
from ranger.scenario import ScenarioHarness, AtomRecord
from ranger.cli import app, _coerce_to_capabilities
from agents.common.runtime import AgentRuntime


@step(inputs=['a'], outputs=['b'])
def inc(s):
    return {'b': s['a'] + 1}


def done(s):
    return s.exists('b')


def atom(id='one', **kw):
    return Atom(id=id, modality='json', content=kw.get('content', {'value': 1}),
                schema='sample@v1', facets=kw.get('facets', {}), provenance={}, policy={})


def test_runs_replay_diff_cli_and_scenario(tmp_path):
    from core.trace import replay_run, diff_runs
    db = tmp_path / 'runs.db'
    agent = Agent([inc], trace_path=db)
    left = agent.run(initial={'a': 1}, goal=done, max_steps=1)
    right = agent.run(initial={'a': 4}, goal=done, max_steps=1)
    assert left.ok and right.ok and left.run_id != right.run_id
    replay = replay_run(db, left.run_id)
    assert replay['state'] == {'a': 1, 'b': 2}
    assert replay['status'] == 'succeeded'
    assert replay_run(db)['run_id'] == right.run_id
    assert [e['seq'] for e in replay['events']] == list(range(len(replay['events'])))
    assert {'initial', 'goal', 'capability_started', 'capability_finished', 'commit', 'outcome'} <= {e['kind'] for e in replay['events']}
    assert diff_runs(db, left.run_id, right.run_id)['changed'] == {'a': {'left': 1, 'right': 4}, 'b': {'left': 2, 'right': 5}}
    for args in [['replay', str(db), '--run-id', left.run_id, '--json'], ['diff', str(db), '--left', left.run_id, '--right', right.run_id, '--json'], ['scenario', str(db), '--run-id', left.run_id, '--json']]:
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)
    assert ScenarioHarness.from_sqlite(db, run_id=left.run_id).generate_report().ok


def test_blocked_failed_and_read_only_replay(tmp_path):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    calls = []
    @tool(outputs=['x'])
    def explode(s):
        calls.append(1)
        raise RuntimeError('tool failed')
    blocked = Agent([inc], budget=Budget(100, 1000, 0), trace_path=db).run(initial={'a': 1}, goal=done)
    assert replay_run(db, blocked.run_id)['status'] == 'blocked'
    with pytest.raises(RuntimeError, match='tool failed'):
        Agent([explode], trace_path=db).run(initial={}, goal=done)
    replay = replay_run(db)
    assert replay['status'] == 'failed'
    assert replay['state'] == {}
    assert calls == [1]
    assert not ScenarioHarness.from_sqlite(db).generate_report().ok
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM execution_events WHERE run_id=? AND seq=1", (replay['run_id'],))
    with pytest.raises(ValueError):
        replay_run(db)
    with pytest.raises((ValueError, KeyError)):
        replay_run(db, 'absent')


@pytest.mark.parametrize('value', [object(), {1: 'bad'}, float('nan'), (1, 2), {1, 2}])
def test_unsupported_initial_values_fail_before_execution(tmp_path, value):
    calls = []
    @step(outputs=['b'])
    def run(s):
        calls.append(1)
        return {'b': 2}
    with pytest.raises((TypeError, ValueError), match='JSON|serializ|finite|unsupported'):
        Agent([run], trace_path=tmp_path / 'runs.db').run(initial={'x': value}, goal=done)
    assert calls == []


def test_record_failure_and_disabled_recording(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    @step(outputs=['b'])
    def run(s):
        calls.append(1)
        return {'b': object()}
    result = Agent([run], trace=False).run(initial={}, goal=done)
    assert result.ok and result.run_id is None
    assert not (tmp_path / '.ranger').exists()
    with pytest.raises((OSError, sqlite3.Error)):
        Agent([inc], trace_path=tmp_path).run(initial={'a': 1}, goal=done)
    with pytest.raises((TypeError, ValueError)):
        Agent([run]).run(initial={}, goal=done)
    from core.trace import replay_run
    assert replay_run(tmp_path / '.ranger/runs.db')['state'] == {}
    assert replay_run(tmp_path / '.ranger/runs.db')['status'] == 'failed'


@pytest.mark.parametrize('payload', [
    {'status': 'failed', 'returncode': 1, 'coverage': {'totals': {'percent_covered': 99}}},
    {'status': 'passed', 'returncode': 0},
    {'status': 'unknown', 'coverage': {'totals': {'percent_covered': 99}}},
])
def test_legacy_scenarios_require_success_and_real_coverage(payload):
    entry = AtomRecord('e', 'testwriter.execution@v1', 'json', payload, {}, {}, 0)
    assert not ScenarioHarness([entry]).generate_report(coverage_target=.8).ok
    with pytest.raises(GoalBlocked):
        ScenarioHarness([entry]).assert_coverage(coverage_target=.8)
    assert not ScenarioHarness([]).generate_report().ok


def test_shared_database_migrates_without_losing_domains(tmp_path):
    db = tmp_path / 'atoms.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE atoms (id TEXT PRIMARY KEY, modality TEXT NOT NULL, content TEXT NOT NULL, schema TEXT, facets TEXT NOT NULL, provenance TEXT NOT NULL, policy TEXT NOT NULL, created_at INTEGER NOT NULL, domain TEXT NOT NULL)')
        conn.execute("INSERT INTO atoms VALUES ('one','json','1',NULL,'{}','{}','{}',0,'left')")
    left = MemSQLite('left', str(db), 'left')
    right = MemSQLite('right', str(db), 'right')
    right.write([atom()])
    assert [a.content for a in left.read({})] == [1]
    assert [a.content for a in right.read({})] == [{'value': 1}]
    MemSQLite('again', str(db), 'left')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM atoms').fetchone()[0] == 2


@pytest.mark.parametrize('facet', ['thread_id', 'unit', 'goal'])
@pytest.mark.parametrize('value', ['a_b', 'a%b', 'a"b'])
def test_exact_json_facet_filters(tmp_path, facet, value):
    memory = MemSQLite('m', str(tmp_path / 'atoms.db'), 'd')
    memory.write([atom('wanted', facets={facet: value}), atom('other', facets={facet: 'axb'})])
    query = {'facets': {facet: value}} if facet == 'thread_id' else {facet: value}
    assert [a.id for a in memory.read(query)] == ['wanted']


def test_memory_context_has_no_phantom_filters(tmp_path):
    from topology.registry import clear_registry, register_region
    from core.engine import Engine
    from core.workspace import Workspace
    memory = MemSQLite('m', str(tmp_path / 'atoms.db'), 'd')
    memory.write([atom('wanted', facets={'domain': 'd'})])
    clear_registry()
    register_region(memory)
    @tool(outputs=['b'])
    def act(s):
        return {'b': 1}
    context = Engine([act])._build_context(act, Workspace({}).snapshot(), done)
    assert any(a.id == 'wanted' for a in context)


def test_summaries_deterministic_and_content_sensitive(tmp_path):
    memory = MemSQLite('m', str(tmp_path / 'atoms.db'), 'd')
    a, b = atom('a'), atom('b', content=2)
    one = memory.summarize([a, b], {'name': 'g'})
    two = memory.summarize([b, a], {'name': 'g'})
    assert one == two
    assert one.id != memory.summarize([a, replace(b, content=3)], {'name': 'g'}).id
    assert memory.summarize([], {}) == memory.summarize([], {})


@pytest.mark.parametrize('field,value', [('id', 'other'), ('modality', 'text'), ('policy', {'pii': False}), ('provenance', {'parents': ['other']})])
def test_security_fields_authenticated(field, value):
    signed = sign_atom(atom(), 'test-key')
    if field == 'provenance':
        value = {**signed.provenance, **value}
    assert not verify_atom(replace(signed, **{field: value}), 'test-key')


def test_chains_reject_truncation_and_legacy_signatures():
    signed = [sign_atom(a, 'key') for a in create_provenance_chain([atom('a'), atom('b')])]
    assert verify_atom_chain(signed, 'key')['valid']
    assert not verify_atom_chain(signed[:1], 'key')['valid']
    assert not verify_atom(replace(signed[0], provenance={**signed[0].provenance, 'signature_version': 'v1'}), 'key')


def test_runtime_and_visualization_preserve_history_and_trace_domain_db(tmp_path):
    class Runtime(AgentRuntime):
        def __init__(self, repo_root):
            super().__init__(repo_root=repo_root, capability_list=[inc], budget=Budget(20000,10000,10), memory_key='m', memory_domain='d', db_filename='domain.db')
    runtime = Runtime(tmp_path)
    memory = MemSQLite('m', str(runtime.db_path), 'd')
    memory.write([atom()])
    result = runtime.run_agent(initial={'a': 1}, goal=done, max_steps=1)
    _coerce_to_capabilities(Runtime, tmp_path)
    assert [a.id for a in memory.read({})] == ['one']
    from core.trace import replay_run
    assert replay_run(runtime.db_path, result.run_id)['state']['b'] == 2


def test_recording_storage_failure_never_reports_success(tmp_path):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    Agent([], trace_path=db).run(initial={}, goal=lambda s: True)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TRIGGER reject_commits BEFORE INSERT ON execution_events
                        WHEN NEW.kind = 'commit' BEGIN SELECT RAISE(FAIL, 'disk failure'); END""")
    with pytest.raises(sqlite3.Error, match='disk failure'):
        Agent([inc], trace_path=db).run(initial={'a': 1}, goal=done)
    with sqlite3.connect(db) as conn:
        status = conn.execute('SELECT status FROM execution_runs ORDER BY started_ns DESC LIMIT 1').fetchone()[0]
    assert status != 'succeeded'
    with pytest.raises(ValueError):
        replay_run(db)


@pytest.mark.parametrize('kind,payload', [
    ('capability_finished', {'capability': 'test_trace_reliability.inc', 'status': 'succeeded', 'duration_ms': -1}),
    ('goal', {'status': 'met'}),
    ('commit', {'capability': 'other', 'state': {'a': 1, 'b': 2}, 'writes': {'b': 2}}),
])
def test_replay_rejects_malformed_event_order_and_payload(tmp_path, kind, payload):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    result = Agent([inc], trace_path=db).run(initial={'a': 1}, goal=done)
    # Replace capability_started with an event that cannot legally occur here.
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE execution_events SET kind=?,payload=? WHERE run_id=? AND seq=2', (kind, json.dumps(payload), result.run_id))
    with pytest.raises(ValueError):
        replay_run(db)


def test_run_selected_scenario_does_not_mix_domains_or_prior_atoms(tmp_path):
    db = tmp_path / '.ranger/shared.db'
    def runtime(domain):
        return AgentRuntime(repo_root=tmp_path, capability_list=[inc], budget=Budget(1000,1000,10), memory_key=domain, memory_domain=domain, db_filename='shared.db')
    left = runtime('left')
    a = left.run_agent(initial={'a': 1}, goal=done, max_steps=1)
    right = runtime('right')
    b = right.run_agent(initial={'a': 3}, goal=lambda s: False, max_steps=0)
    report = ScenarioHarness.from_sqlite(db, domain='left').generate_report()
    assert report.ok and report.run_id == a.run_id
    assert not ScenarioHarness.from_sqlite(db, domain='right').generate_report().ok
    with pytest.raises(ValueError):
        ScenarioHarness.from_sqlite(db, domain='left', run_id=b.run_id)


def test_diff_added_removed_and_merged_snapshot(tmp_path):
    from core.trace import replay_run, diff_runs
    from core.merge import WriteSpec, MergeMode
    db = tmp_path / 'runs.db'
    @step(outputs=['nested'], write_specs={'nested': WriteSpec(merge_mode=MergeMode.MERGE_JSON)})
    def patch(s):
        return {'nested': {'b': 2}}
    result = Agent([patch], trace_path=db).run(initial={'nested': {'a': 1}}, goal=lambda s: s['nested'].get('b') == 2)
    assert replay_run(db)['state'] == {'nested': {'a': 1, 'b': 2}}
    other = Agent([], trace_path=db).run(initial={'new': [1, None, True]}, goal=lambda s: True)
    diff = diff_runs(db, result.run_id, other.run_id)
    assert diff['added'] == {'new': [1, None, True]}
    assert diff['removed'] == {'nested': {'a': 1, 'b': 2}}


def test_migration_refuses_unknown_schema_without_row_loss(tmp_path):
    db = tmp_path / 'custom.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE atoms (id TEXT PRIMARY KEY, custom TEXT)')
        conn.execute("INSERT INTO atoms VALUES ('one', 'retained')")
    with pytest.raises(ValueError, match='schema'):
        MemSQLite('m', str(db), 'd')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM atoms').fetchall() == [('one', 'retained')]


def test_legacy_atoms_bound_to_run_cannot_be_overwritten_by_later_run(tmp_path):
    db = tmp_path / 'atoms.db'
    memory = MemSQLite('m', str(db), 'd')
    @step(inputs=['passed'], outputs=['b'])
    def record(s):
        content = {'status': 'passed' if s['passed'] else 'failed', 'returncode': 0 if s['passed'] else 1,
                   'coverage': {'totals': {'percent_covered': 99}}}
        memory.write([replace(atom('same-id', content=content), schema='testwriter.execution@v1')])
        return {'b': 1}
    agent = Agent([record], trace_path=db)
    left = agent.run(initial={'passed': True}, goal=done)
    agent.run(initial={'passed': False}, goal=done)
    assert ScenarioHarness.from_sqlite(db, run_id=left.run_id).generate_report(coverage_target=.8).ok
    assert not ScenarioHarness.from_sqlite(db).generate_report(coverage_target=.8).ok


def test_migration_preserves_custom_indexes_and_triggers(tmp_path):
    db = tmp_path / 'custom.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE atoms (id TEXT PRIMARY KEY, modality TEXT NOT NULL, content TEXT NOT NULL, schema TEXT, facets TEXT NOT NULL, provenance TEXT NOT NULL, policy TEXT NOT NULL, created_at INTEGER NOT NULL, domain TEXT NOT NULL)')
        conn.execute('CREATE INDEX content_search ON atoms(content)')
        conn.execute('CREATE TABLE audit (id TEXT)')
        conn.execute('CREATE TRIGGER audit_insert AFTER INSERT ON atoms BEGIN INSERT INTO audit VALUES (NEW.id); END')
        conn.execute("INSERT INTO atoms VALUES ('old','json','1',NULL,'{}','{}','{}',0,'d')")
    memory = MemSQLite('m', str(db), 'd')
    memory.write([atom('new')])
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='content_search'").fetchone()
        assert conn.execute('SELECT id FROM audit').fetchall() == [('old',), ('new',)]


def test_malformed_memory_event_rejected_by_replay(tmp_path):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    result = Agent([inc], trace_path=db).run(initial={'a': 1}, goal=done)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE execution_events SET kind='memory_atom', payload=? WHERE run_id=? AND seq=1",
                     (json.dumps({'domain': 'd', 'atom': {}}), result.run_id))
    with pytest.raises(ValueError):
        replay_run(db)


def test_circular_state_rejected_explicitly(tmp_path):
    circular = []
    circular.append(circular)
    with pytest.raises(TypeError, match='JSON|circular'):
        Agent([], trace_path=tmp_path / 'runs.db').run(initial={'circular': circular}, goal=lambda s: True)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('consumer_kind', ['compute', 'action', 'model'])
def test_recency_prevents_writer_starvation_and_resets_each_run(tmp_path, reverse, consumer_kind):
    from core.sdk import llm
    calls = []
    @step(inputs=['n'], outputs=['n'], name='a.loop')
    def loop(s):
        calls.append(('loop', s['n']))
        return {'n': s['n'] + 1}
    if consumer_kind == 'model':
        class Provider:
            def generate(self, **kwargs):
                calls.append(('consume', int(kwargs['prompt'])))
                return kwargs['prompt']
        @llm(inputs=['n'], outputs=['done'], name='b.consume', provider=Provider(), template='{{ n }}')
        def consume(s):
            pass
    else:
        decorator = step if consumer_kind == 'compute' else tool
        @decorator(inputs=['n'], outputs=['done'], name='b.consume')
        def consume(s):
            calls.append(('consume', s['n']))
            return {'done': s['n']}
    capabilities = [consume, loop] if reverse else [loop, consume]
    agent = Agent(capabilities, trace_path=tmp_path / 'runs.db', budget=Budget(20000,10000,8))
    for initial in (0, 10):
        result = agent.run(initial={'n': initial}, goal=lambda s: s.exists('done'), max_steps=8)
        assert result.ok
        assert int(result.final['done']) == initial + 1
    assert calls == [('loop', 0), ('consume', 1), ('loop', 10), ('consume', 11)]


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('skip_kind', ['empty_writes', 'skip_llm'])
def test_noop_does_not_terminate_before_deferred_consumer(tmp_path, reverse, skip_kind):
    from core.runners.llm_runner import SkipLLM
    from core.sdk import llm
    calls = []
    if skip_kind == 'skip_llm':
        class Provider:
            def generate(self, **kwargs):
                raise AssertionError('Skipped LLM must not invoke provider')
        def skip(s):
            calls.append('noop')
            raise SkipLLM()
        @llm(inputs=['n'], outputs=['n'], name='a.noop', provider=Provider(), map=skip)
        def noop(s):
            pass
        consumer_decorator = tool
    else:
        @step(inputs=['n'], outputs=['n'], name='a.noop')
        def noop(s):
            calls.append('noop')
            return {}
        consumer_decorator = step
    @consumer_decorator(inputs=['n'], outputs=['done'], name='b.consume')
    def consume(s):
        calls.append('consume')
        return {'done': s['n']}
    caps = [consume, noop] if reverse else [noop, consume]
    result = Agent(caps, trace_path=tmp_path / 'runs.db').run(initial={'n': 0}, goal=lambda s: s.exists('done'), max_steps=4)
    assert result.ok and result.final['done'] == 0
    assert calls == ['noop', 'consume']


def test_noop_missing_output_is_suppressed_until_reads_change(tmp_path):
    calls = []
    @step(inputs=['n'], outputs=['missing'], name='a.noop')
    def noop(s):
        calls.append(s['n'])
        return {}
    result = Agent([noop], trace_path=tmp_path / 'single.db').run(initial={'n': 0}, goal=lambda s: False, max_steps=20)
    assert not result.ok and result.blocker.reason == 'no_progress'
    assert calls == [0]
    @step(inputs=['n'], outputs=['n'], name='b.advance')
    def advance(s):
        return {'n': s['n'] + 1}
    calls.clear()
    result = Agent([noop, advance], trace_path=tmp_path / 'loop.db').run(initial={'n': 0}, goal=lambda s: s['n'] == 2, max_steps=4)
    assert result.ok
    assert calls == [0, 1]


@pytest.mark.parametrize('finish_status', ['failed', 'blocked'])
def test_replay_rejects_terminal_capability_failure_followed_by_success(tmp_path, finish_status):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    result = Agent([inc], trace_path=db).run(initial={'a': 1}, goal=done)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT seq,payload FROM execution_events WHERE run_id=? AND kind='capability_finished'", (result.run_id,)).fetchone()
        payload = {**json.loads(row[1]), 'status': finish_status, 'error': 'terminal failure'}
        conn.execute('UPDATE execution_events SET payload=? WHERE run_id=? AND seq=?', (json.dumps(payload), result.run_id, row[0]))
    with pytest.raises(ValueError):
        replay_run(db, result.run_id)
    with pytest.raises(ValueError):
        ScenarioHarness.from_sqlite(db, run_id=result.run_id)
    cli = CliRunner().invoke(app, ['scenario', str(db), '--run-id', result.run_id, '--json'])
    assert cli.exit_code != 0


@pytest.mark.parametrize('corruption', ['early_met', 'stale_met', 'wrong_steps', 'goal_failed', 'goal_blocked'])
def test_success_requires_terminal_goal_for_final_state(tmp_path, corruption):
    from core.trace import replay_run
    db = tmp_path / 'runs.db'
    result = Agent([inc], trace_path=db).run(initial={'a': 1}, goal=done)
    with sqlite3.connect(db) as conn:
        events = conn.execute('SELECT seq,kind,payload FROM execution_events WHERE run_id=? ORDER BY seq', (result.run_id,)).fetchall()
        for seq, kind, raw in events:
            payload = json.loads(raw)
            if corruption in ('early_met', 'stale_met') and kind == 'goal':
                payload['status'] = 'met' if payload['step'] == 0 else ('unmet' if corruption == 'stale_met' else 'met')
            elif corruption == 'wrong_steps' and kind == 'outcome':
                payload['steps'] += 1
            elif corruption in ('goal_failed', 'goal_blocked') and kind == 'goal' and payload['step'] == 1:
                payload['status'] = corruption.removeprefix('goal_')
            conn.execute('UPDATE execution_events SET payload=? WHERE run_id=? AND seq=?', (json.dumps(payload), result.run_id, seq))
    with pytest.raises(ValueError):
        replay_run(db)
    with pytest.raises(ValueError):
        ScenarioHarness.from_sqlite(db)


def test_legal_soft_retry_and_terminal_failure_keep_earlier_commits(tmp_path):
    from core.trace import replay_run
    attempts = []
    @step(outputs=['b'], name='a.retry')
    def retry(s):
        attempts.append(1)
        if len(attempts) == 1:
            raise GoalBlocked('llm_invalid_output')
        return {'b': 2}
    db = tmp_path / 'runs.db'
    success = Agent([retry], trace_path=db).run(initial={}, goal=done)
    assert success.ok and replay_run(db)['state'] == {'b': 2}
    assert [e['payload']['status'] for e in replay_run(db)['events'] if e['kind'] == 'capability_finished'] == ['retry', 'succeeded']
    @step(outputs=['a'], name='a.commit')
    def first(s):
        return {'a': 1}
    for error in (GoalBlocked('policy_blocked'), RuntimeError('failed')):
        @step(outputs=['z'], name='z.fail')
        def fail(s):
            raise error
        agent = Agent([first, fail], trace_path=db)
        if isinstance(error, GoalBlocked):
            assert not agent.run(initial={}, goal=done).ok
            expected = 'blocked'
        else:
            with pytest.raises(RuntimeError):
                agent.run(initial={}, goal=done)
            expected = 'failed'
        replay = replay_run(db)
        assert replay['status'] == expected and replay['state'] == {'a': 1}


def test_runtime_overrides_use_actual_domain_key_and_failed_evidence(tmp_path):
    from topology.registry import get_region
    @step(outputs=['b'])
    def write_failed(s):
        memory = get_region('actual-key')
        memory.write([replace(atom(content={'status': 'failed', 'returncode': 1,
                    'coverage': {'totals': {'percent_covered': 99}}}), schema='testwriter.execution@v1')])
        return {'b': 2}
    runtime = AgentRuntime(repo_root=tmp_path, capability_list=[write_failed], budget=Budget(10000,10000,10),
                           memory_key='declared-key', memory_domain='declared-domain', db_filename='runs.db',
                           memory_kwargs={'memory_key': 'actual-key', 'domain': 'actual-domain'})
    result = runtime.run_agent(initial={}, goal=done, max_steps=1)
    report = runtime.scenario_report(run_id=result.run_id)
    assert not report.ok and report.atoms and report.coverage.actual == .99
    assert runtime.memory_domain == 'actual-domain' and runtime.memory_key == 'actual-key'
    assert runtime.agent.engine.trace_domain == 'actual-domain'
    assert not ScenarioHarness.from_sqlite(runtime.db_path, domain='actual-domain', run_id=result.run_id).generate_report().ok


@pytest.mark.parametrize('same_domain', [False, True])
def test_summary_canonicalizes_duplicate_id_sources(tmp_path, same_domain):
    memory = MemSQLite('m', str(tmp_path / 'atoms.db'), 'd')
    a = atom('same', content={'left': 1}, facets={'domain': 'left'})
    b = atom('same', content={'right': 2}, facets={'domain': 'left' if same_domain else 'right'})
    assert memory.summarize([a, b], {}) == memory.summarize([b, a], {})
