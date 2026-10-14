"""Final review regressions at public execution, filesystem and memory boundaries."""
import copy
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from tests.test_reference_agent_reliability import writer, SuiteProvider, invoke
from tests.test_deep_research_agent import _offline_agent
from agents.testwriter import capabilities as tw
from core.sdk import Agent, tool, step
from core.execution import generate_with_policy
from core.workspace import Workspace
from core.trace import replay_run
from core.errors import GoalBlocked
from ranger.scenario import ScenarioHarness
from ranger.cli import app
from regions.guard_pii import GuardPII
from topology.registry import clear_registry, register_region


class PassingProvider:
    def __init__(self, shared=False):
        self.shared = shared
        self.calls = 0

    def generate(self, **request):
        self.calls += 1
        module = 'mod2' if 'pkg.mod2' in request['prompt'] else 'mod1'
        path = 'tests/test_shared.py' if self.shared else f'tests/test_{module}.py'
        content = f'from pkg.{module} import value\n\ndef test_value():\n    assert value() == 1\n'
        return json.dumps({'tests': [{'path': path, 'content': content}]})


def test_public_writer_cannot_replace_another_certified_target(tmp_path):
    agent = writer(tmp_path, PassingProvider(shared=True), targets=2, attempts=1)
    result = agent.run()
    assert not result.ok
    assert 'pkg.mod1' in (tmp_path / 'tests/test_shared.py').read_text()
    assert not agent.scenario_report(run_id=result.run_id).ok


@pytest.mark.parametrize('alias', ['tests/test_shared.py', 'tests/alias.py', 'tests/./test_shared.py'])
def test_owned_artifact_rejection_preflights_entire_batch(tmp_path, alias):
    agent = writer(tmp_path, PassingProvider(shared=True))
    result = agent.run()
    assert result.ok
    original = (tmp_path / 'tests/test_shared.py').read_text()
    (tmp_path / 'tests/alias.py').symlink_to('test_shared.py')
    entry = {'path': 'pkg/mod2.py', 'attempt': 1}
    identity = {'target': 'pkg/mod2.py', 'attempt': 1}
    requests = [{'path': 'tests/test_new.py', 'content': 'new', 'identity': identity},
                {'path': alias, 'content': 'replacement', 'identity': identity}]
    with pytest.raises(GoalBlocked):
        invoke(tw.write_test_files, {'repo.root': str(tmp_path), 'tests.active.entry': entry,
                                   'tests.progress': result.final.get('tests.progress'), 'tests.write.requests': requests})
    assert not (tmp_path / 'tests/test_new.py').exists()
    assert (tmp_path / 'tests/test_shared.py').read_text() == original


def test_certification_binds_artifact_content(tmp_path):
    agent = writer(tmp_path, PassingProvider())
    result = agent.run()
    assert result.ok
    state = {k: v.value for k, v in result.final.data.items()}
    path = tmp_path / 'tests/test_mod1.py'
    path.write_text('def test_unrelated():\n    assert True\n')
    state.pop('tests.terminal', None)
    terminal = invoke(tw.detect_terminal_state, state).get('tests.terminal')
    assert terminal != {'status': 'success'}


@pytest.mark.parametrize('low_module', ['mod1', 'mod2'])
def test_run_coverage_override_checks_every_final_target(tmp_path, low_module):
    agent = writer(tmp_path, PassingProvider(), targets=2)
    (tmp_path / f'pkg/{low_module}.py').write_text('def value(flag=True):\n    if flag:\n        return 1\n    return 2\n')
    high_module = 'mod2' if low_module == 'mod1' else 'mod1'
    (tmp_path / f'pkg/{high_module}.py').write_text('def value():\n    # equal source line count\n    # deterministic target order\n    return 1\n')
    result = agent.run()
    assert result.ok, result.blocker
    execution = replay_run(agent.db_path, result.run_id)
    targets = [e['payload']['atom']['content']['identity']['target'] for e in execution['events']
               if e['kind'] == 'memory_atom' and e['payload']['atom']['schema'] == 'testwriter.execution@v1']
    assert targets == ['pkg/mod1.py', 'pkg/mod2.py']
    report = agent.scenario_report(run_id=result.run_id, coverage_target=.9)
    assert not report.ok
    assert report.coverage.actual == .75
    assert not report.coverage.passed
    assert any(low_module in p for p in report.coverage.missing_files)
    cli = CliRunner().invoke(app, ['scenario', str(agent.db_path), '--run-id', result.run_id, '--coverage-target', '.9', '--json'])
    assert not json.loads(cli.output)['ok']


@pytest.mark.parametrize('damage', ['missing', 'attempt', 'artifact', 'coverage'])
def test_run_scenario_requires_matching_final_evidence(tmp_path, damage):
    agent = writer(tmp_path, PassingProvider(), targets=2)
    result = agent.run()
    assert result.ok
    harness = ScenarioHarness.from_sqlite(agent.db_path, run_id=result.run_id)
    atoms = copy.deepcopy(list(harness.atoms))
    atom = next(a for a in atoms if a.schema == 'testwriter.execution@v1' and a.content['identity']['target'] == 'pkg/mod1.py')
    if damage == 'missing':
        atoms.remove(atom)
    elif damage == 'attempt':
        atom.content['identity']['attempt'] += 1
    elif damage == 'artifact':
        atom.content['artifacts'] = [{'path': 'tests/other.py', 'sha256': 'unrelated'}]
    else:
        atom.content.pop('coverage')
    assert not ScenarioHarness(atoms, execution=harness.execution).generate_report(coverage_target=.9).ok


def test_run_scenario_uses_successful_final_repair(tmp_path):
    agent = writer(tmp_path, SuiteProvider(repair=True), attempts=3)
    result = agent.run()
    assert result.ok
    assert agent.scenario_report(run_id=result.run_id, coverage_target=.9).ok


def assert_configured_memory(agent, result, schema):
    replay = replay_run(agent.db_path, result.run_id)
    evidence = [e['payload'] for e in replay['events'] if e['kind'] == 'memory_atom']
    assert evidence
    assert all(e['domain'] == 'custom-domain' and e['atom']['facets']['domain'] == 'custom-domain' for e in evidence)
    assert any(e['atom']['schema'] == schema for e in evidence)
    with sqlite3.connect(agent.db_path) as conn:
        assert conn.execute('SELECT count(*) FROM atoms WHERE domain=?', ('custom-domain',)).fetchone()[0] > 0
    @tool(outputs=['unused'])
    def reader(s):
        return {'unused': True}
    atoms = agent.agent.engine._build_context(reader, Workspace({}).snapshot(), lambda s: False)
    assert any(a.schema == schema for a in atoms)
    assert all(a.facets.get('domain') == 'custom-domain' for a in atoms)


@pytest.mark.parametrize('passing', [True, False])
def test_public_writer_effective_memory_key_and_domain(tmp_path, passing):
    provider = PassingProvider() if passing else SuiteProvider()
    agent = writer(tmp_path, provider, targets=1 if passing else 2, attempts=1,
                   memory_kwargs={'memory_key': 'custom.memory', 'domain': 'custom-domain'})
    result = agent.run()
    assert result.ok is passing
    assert_configured_memory(agent, result, 'testwriter.execution@v1')
    assert agent.scenario_report(run_id=result.run_id, coverage_target=.5).ok is passing


def test_public_research_effective_memory_key_and_domain(tmp_path, monkeypatch):
    agent = _offline_agent(tmp_path, monkeypatch, memory_kwargs={'memory_key': 'custom.memory', 'domain': 'custom-domain'})
    result = agent.run()
    assert result.ok, result.blocker
    assert_configured_memory(agent, result, 'deepresearch.report@v1')


@pytest.mark.parametrize('records', [
    {'alice@example.com': 1, 'bob@example.com': 2},
    {'outer': {'alice@example.com': 1, 'bob@example.com': 2}},
    {'alice@example.com': 1, '[EMAIL_REDACTED]': 2},
    {'alice@example.com 123-45-6789': 1, '[EMAIL_REDACTED] [SSN_REDACTED]': 2},
])
@pytest.mark.parametrize('boundary', ['action', 'model'])
def test_structured_pii_key_collisions_block_before_execution(tmp_path, records, boundary):
    clear_registry()
    register_region(GuardPII('pii'))
    calls = []
    class Provider:
        def generate(self, **request):
            calls.append(request)
            return 'ok'
    if boundary == 'action':
        @tool(inputs=['records'], outputs=['done'])
        def protected(s):
            calls.append(s.get('records'))
            return {'done': True}
        initial = {'records': records}
    else:
        @step(outputs=['done'])
        def protected(s):
            generate_with_policy(Provider(), prompt=records, max_tokens=100)
            return {'done': True}
        initial = {}
    try:
        result = Agent([protected], trace_path=tmp_path / 'runs.db').run(initial=initial, goal=lambda s: s.exists('done'))
        assert not result.ok
        assert result.blocker.reason == 'policy_blocked'
        assert not calls
    finally:
        clear_registry()


def test_noncolliding_pii_keys_preserve_all_records(tmp_path):
    clear_registry()
    register_region(GuardPII('pii'))
    received = []
    @tool(inputs=['records'], outputs=['done'])
    def protected(s):
        received.append(s.get('records'))
        return {'done': True}
    try:
        result = Agent([protected], trace_path=tmp_path / 'runs.db').run(
            initial={'records': {'alice@example.com': {'n': 1}, 'ordinary': {'n': 2}}},
            goal=lambda s: s.exists('done'))
        assert result.ok
        assert received == [{'[EMAIL_REDACTED]': {'n': 1}, 'ordinary': {'n': 2}}]
    finally:
        clear_registry()


def test_effective_memory_binding_does_not_fall_back(tmp_path):
    from regions.mem_sqlite import MemSQLite
    agent = writer(tmp_path, PassingProvider(), memory_kwargs={'memory_key': 'custom.memory', 'domain': 'custom-domain'})
    clear_registry()
    unrelated = MemSQLite('testwriter.memory', str(tmp_path / 'unrelated.db'), 'testwriter')
    register_region(unrelated)
    try:
        with pytest.raises(RuntimeError, match='Configured reference memory unavailable'):
            agent.run()
        assert not list(unrelated.read({}))
        assert replay_run(agent.db_path)['status'] == 'failed'
    finally:
        clear_registry()


def test_legacy_scenario_retains_latest_execution_semantics():
    from ranger.scenario import AtomRecord
    failed = AtomRecord('old', 'testwriter.execution@v1', 'json',
                        {'status': 'failed', 'returncode': 1, 'coverage': {'totals': {'percent_covered': 20}}}, {}, {}, 0)
    passed = AtomRecord('new', 'testwriter.execution@v1', 'json',
                        {'status': 'passed', 'returncode': 0, 'coverage': {'totals': {'percent_covered': 100}}}, {}, {}, 1)
    assert ScenarioHarness([failed, passed]).generate_report(coverage_target=.9).ok
    assert not ScenarioHarness([passed, failed]).generate_report(coverage_target=.9).ok


@pytest.mark.parametrize('selector', ['root', 'test_path'])
def test_sdk_writer_guard_cannot_change_filesystem_selectors(tmp_path, selector):
    root = tmp_path / ('alice@example.com/repo' if selector == 'root' else 'repo')
    root.mkdir(parents=True)
    path = 'tests/test_generated.py' if selector == 'root' else 'tests/alice@example.com/test_generated.py'
    entry = {'path': 'pkg/mod.py', 'attempt': 1}
    clear_registry()
    register_region(GuardPII('pii'))
    try:
        result = Agent([tw.write_test_files], trace=False).run(
            initial={'repo.root': str(root), 'tests.active.entry': entry, 'tests.progress': {},
                     'tests.write.requests': [{'path': path, 'content': 'assert True\n', 'identity': tw._identity(entry)}]},
            goal=lambda s: s.exists('tests.write.result'))
        assert not list(tmp_path.rglob('test_generated.py'))
        assert not result.ok
        assert result.blocker.reason == 'policy_blocked'
    finally:
        clear_registry()


def test_public_writer_guard_preserves_original_root(tmp_path):
    root = tmp_path / 'alice@example.com/repo'
    root.mkdir(parents=True)
    agent = writer(root, PassingProvider())
    register_region(GuardPII('pii'))
    try:
        result = agent.run()
        assert not (tmp_path / '[EMAIL_REDACTED]').exists()
        assert not list(root.rglob('test_mod1.py'))
        assert not result.ok
        assert result.blocker.reason == 'policy_blocked'
    finally:
        clear_registry()


def test_sdk_research_guard_cannot_relocate_report(tmp_path):
    from agents.deep_research import capabilities as dr
    from agents.deep_research.types import DeepResearchConfig
    output = tmp_path / 'alice@example.com/report.md'
    clear_registry()
    register_region(GuardPII('pii'))
    try:
        result = Agent([dr.persist_report], trace=False).run(
            initial={'research.report': 'Evidence report', 'research.citations': [],
                     'deepresearch.config': DeepResearchConfig(output_path=output).to_state()},
            goal=lambda s: s.exists('research.output_path'))
        assert not list(tmp_path.rglob('report.md'))
        assert not result.ok
        assert result.blocker.reason == 'policy_blocked'
    finally:
        clear_registry()


def test_sdk_writer_preserves_routing_while_masking_content(tmp_path):
    entry = {'path': 'pkg/mod.py', 'attempt': 1}
    clear_registry()
    register_region(GuardPII('pii'))
    try:
        result = Agent([tw.write_test_files], trace=False).run(
            initial={'repo.root': str(tmp_path), 'tests.active.entry': entry, 'tests.progress': {},
                     'tests.write.requests': [{'path': 'tests/test_generated.py', 'content': '# alice@example.com\nassert True\n', 'identity': tw._identity(entry)}]},
            goal=lambda s: s.exists('tests.write.result'))
        assert result.ok, result.blocker
        assert (tmp_path / 'tests/test_generated.py').read_text() == '# [EMAIL_REDACTED]\nassert True\n'
    finally:
        clear_registry()


@pytest.mark.parametrize('existing_newline,requested_newline', [
    ('\r\n', '\n'),
    ('\n', '\n'),
    ('\r\n', '\r\n'),
    (None, '\n'),
    (None, '\r\n'),
])
def test_sdk_writer_certifies_exact_requested_utf8_bytes(tmp_path, existing_newline, requested_newline):
    from agents.testwriter.types import TestWriterConfig as WriterConfig
    import hashlib
    clear_registry()
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg/__init__.py').write_bytes(b'')
    (tmp_path / 'pkg/mod.py').write_bytes(b'def value():\n    return 1\n')
    (tmp_path / 'tests').mkdir()
    target = tmp_path / 'tests/test_mod.py'
    text = '# caf\u00e9\nfrom pkg.mod import value\n\ndef test_value():\n    assert value() == 1\n'
    content = text.replace('\n', requested_newline)
    if existing_newline is not None:
        target.write_bytes(text.replace('\n', existing_newline).encode('utf-8'))
    entry = {'path': 'pkg/mod.py', 'module': 'pkg.mod', 'attempt': 1}
    result = Agent([tw.write_test_files, tw.run_selected_tests], trace_path=tmp_path / 'run.db').run(
        initial={'repo.root': str(tmp_path), 'tests.active.entry': entry, 'tests.progress': {},
                 'testwriter.config': WriterConfig().to_state(),
                 'tests.write.requests': [{'path': 'tests/test_mod.py', 'content': content, 'identity': tw._identity(entry)}]},
        goal=lambda s: s.exists('tests.run.result'))
    assert result.ok, result.blocker
    assert result.final.get('tests.run.result')['status'] == 'passed'
    assert target.read_bytes() == content.encode('utf-8')
    write = result.final.get('tests.write.result')
    unchanged = existing_newline == requested_newline
    assert write['skipped'] == (['tests/test_mod.py'] if unchanged else [])
    assert write['written'] == ([] if unchanged else ['tests/test_mod.py'])
    assert write['artifacts'] == [{'path': 'tests/test_mod.py', 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}]
    assert result.final.get('tests.run.result')['artifacts'] == write['artifacts']


def test_sdk_executor_still_blocks_real_byte_change(tmp_path):
    from agents.testwriter.types import TestWriterConfig as WriterConfig
    clear_registry()
    entry = {'path': 'pkg/mod.py', 'module': 'pkg.mod', 'attempt': 1}
    initial = {'repo.root': str(tmp_path), 'tests.active.entry': entry, 'tests.progress': {},
               'testwriter.config': WriterConfig().to_state(),
               'tests.write.requests': [{'path': 'tests/test_mod.py', 'content': 'def test_ok():\n    assert True\n', 'identity': tw._identity(entry)}]}
    written = Agent([tw.write_test_files], trace=False).run(initial=initial, goal=lambda s: s.exists('tests.write.result'))
    assert written.ok
    (tmp_path / 'tests/test_mod.py').write_bytes(b'def test_changed():\n    assert True\n')
    result = Agent([tw.run_selected_tests], trace=False).run(
        initial={**initial, 'tests.write.result': written.final.get('tests.write.result')},
        goal=lambda s: s.exists('tests.run.result'))
    assert not result.ok
    assert result.blocker.reason == 'test_artifact_changed'
    assert not result.final.exists('tests.run.result')
    assert not (tmp_path / tw.COVERAGE_PATH).exists()
