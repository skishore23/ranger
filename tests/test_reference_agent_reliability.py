"""Reference workflows use real files/pytest and deterministic external providers."""
import json
from pathlib import Path

import pytest

from agents.testwriter.agent import TestWriterAgent
from agents.testwriter.types import TestWriterConfig as WriterConfig
from agents.testwriter import capabilities as tw
from agents.deep_research.agent import DeepResearchAgent
from agents.deep_research.types import DeepResearchConfig
from agents.deep_research import capabilities as dr
from agents.deep_research import firecrawl
from core.workspace import Workspace
from core.errors import GoalBlocked
from core.llm.provider import register_llm_profile
from boot import get_default_budget


def snap(values):
    return Workspace(values).snapshot()


def invoke(cap, values):
    return cap.runner.run(cap, snap(values))


@pytest.mark.parametrize('bad', ['../outside.py', 'ABSOLUTE', 'pkg/mod1.py', 'tests/../pkg/mod1.py', 'tests/link/escape.py', 'tests/escape.py'])
def test_writer_validates_whole_batch_before_writes(tmp_path, bad):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'tests').mkdir()
    (root / 'pkg').mkdir()
    outside = tmp_path / 'outside.py'
    outside.write_text('original')
    (root / 'pkg/mod1.py').write_text('original')
    (root / 'tests/link').symlink_to(tmp_path, target_is_directory=True)
    (root / 'tests/escape.py').symlink_to(outside)
    if bad == 'ABSOLUTE':
        bad = str(outside)
    requests = [{'path': 'tests/test_good.py', 'content': 'assert True'}, {'path': bad, 'content': 'overwritten'}]
    with pytest.raises(GoalBlocked):
        invoke(tw.write_test_files, {'repo.root': str(root), 'tests.write.requests': requests})
    assert not (root / 'tests/test_good.py').exists()
    assert outside.read_text() == 'original'
    assert (root / 'pkg/mod1.py').read_text() == 'original'


def test_writer_rejects_tests_root_symlink(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'tests').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GoalBlocked):
        invoke(tw.write_test_files, {'repo.root': str(root), 'tests.write.requests': [{'path': 'tests/escape.py', 'content': 'unsafe'}]})
    assert not (tmp_path / 'escape.py').exists()


class SuiteProvider:
    def __init__(self, repair=False, empty=False):
        self.repair, self.empty = repair, empty
        self.prompts = []

    def generate(self, **request):
        prompt = request['prompt']
        self.prompts.append(prompt)
        if self.empty:
            return json.dumps({'tests': []})
        module = 'mod2' if 'pkg.mod2' in prompt else 'mod1'
        passing = module == 'mod1' and (not self.repair or len(self.prompts) > 1)
        return json.dumps({'tests': [{'path': f'tests/test_{module}.py', 'content': f'from pkg.{module} import value\n\ndef test_value():\n    assert value() == {1 if passing else 2}\n'}]})


def writer(tmp_path, provider, *, targets=1, attempts=2, coverage=.5):
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg/__init__.py').write_text('')
    for i in range(1, targets + 1):
        (tmp_path / f'pkg/mod{i}.py').write_text('def value():\n    return 1\n')
    agent = TestWriterAgent(repo_root=tmp_path, config=WriterConfig(include=['pkg/*.py'], max_attempts_per_target=attempts, coverage_target=coverage))
    register_llm_profile('testwriter.generation', provider=provider, defaults={'model': 'stub', 'max_tokens': 1600})
    return agent


def test_public_writer_second_suite_failure_is_authoritative(tmp_path):
    agent = writer(tmp_path, SuiteProvider(), targets=2, attempts=1)
    result = agent.run()
    assert (tmp_path / 'tests/test_mod1.py').exists()
    progress = result.final.get('tests.progress')
    assert progress['pkg/mod1.py']['status'] == 'passed'
    assert progress['pkg/mod2.py']['status'] != 'passed'
    assert not result.ok
    assert not agent.scenario_report().ok


def test_public_writer_repairs_one_target(tmp_path):
    provider = SuiteProvider(repair=True)
    agent = writer(tmp_path, provider, attempts=3)
    result = agent.run()
    assert result.ok, result.blocker
    assert result.final.get('tests.progress')['pkg/mod1.py']['attempts'] == 2
    assert 'repair' in provider.prompts[1].lower()
    assert agent.scenario_report().ok


def test_public_writer_preserves_attempt_limit(tmp_path):
    provider = SuiteProvider()
    agent = writer(tmp_path, provider, targets=2, attempts=3)
    result = agent.run()
    assert not result.ok
    assert result.final.get('tests.progress')['pkg/mod2.py']['attempts'] == 3


def test_public_writer_failed_generation_cannot_certify_target(tmp_path):
    agent = writer(tmp_path, SuiteProvider(empty=True))
    result = agent.run()
    assert not result.ok
    assert not (tmp_path / 'tests/test_mod1.py').exists()
    assert not agent.scenario_report().ok


@pytest.mark.parametrize('coverage', [None, {'totals': {'percent_covered': 20}, 'files': {}}])
def test_missing_or_low_coverage_does_not_pass(coverage):
    result = invoke(tw.analyze_coverage, {'tests.run.result': {'status': 'passed', 'coverage': coverage}, 'testwriter.config': {'coverage_target': .5}})
    assert not result['coverage.goal.met']
    assert result['coverage.report']['total'] <= .2


def test_firecrawl_documented_contract(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'success': True, 'data': [{'url': 'https://example.org/a', 'title': 'A', 'description': 'summary', 'markdown': 'retrieved text'}]}
    sent = {}
    def post(url, **kwargs):
        sent.update(kwargs)
        return Response()
    monkeypatch.setattr(firecrawl.httpx, 'post', post)
    sources = firecrawl.fetch_sources('query', api_key='offline', max_results=3)
    assert sent['json']['limit'] == 3
    assert 'num_results' not in sent['json']
    assert sources[0]['snippet'] == 'summary'
    assert sources[0]['content'] == 'retrieved text'


def test_firecrawl_provider_error_surfaces(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'success': False, 'error': 'provider unavailable'}
    monkeypatch.setattr(firecrawl.httpx, 'post', lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match='provider unavailable'):
        firecrawl.fetch_sources('query', api_key='offline')


def research_values(config=None):
    config = config or DeepResearchConfig(desired_length_pages=1, min_citations=1)
    return {'deepresearch.config': config.to_state(), 'research.request': {'topic': 'Topic'}, 'research.sources': [{'id': 'source-a', 'url': 'https://example.org/a', 'title': 'Evidence', 'snippet': 'Source fact'}], 'research.plan': {'sections': [], 'research_questions': []}, 'research.notes': {'key_findings': ['Fact']}, 'research.draft': 'review me', 'research.citations': []}


def test_required_review_constructs_and_missing_feedback_waits(tmp_path):
    config = DeepResearchConfig(require_human_feedback=True)
    agent = DeepResearchAgent(repo_root=tmp_path, topic='Topic', config=config)
    result = invoke(dr.ensure_feedback, research_values(config))
    assert result == {}
    with pytest.raises(GoalBlocked, match='human'):
        invoke(dr.integrate_feedback, research_values(config))
    with pytest.raises(GoalBlocked, match='human'):
        dr.human_review.runner.callback({}, snap(research_values(config)))


def test_research_notes_and_draft_prompts_preserve_source_identity(monkeypatch):
    prompts = []
    def model(**request):
        prompts.append(request['prompt'])
        if request['stage'] == 'notes':
            return {'key_findings': ['Fact']}
        return {'draft': meaningful_report(), 'citations': [{'label': 'A', 'title': 'Evidence', 'url': 'https://example.org/a'}]}
    monkeypatch.setattr(dr, '_call_model', model)
    notes = invoke(dr.synthesize_notes, research_values())
    assert 'https://example.org/a' in prompts[0] and 'source-a' in prompts[0]
    assert notes['research.notes']['sources'][0]['id'] == 'source-a'
    invoke(dr.draft_report, research_values())
    assert 'https://example.org/a' in prompts[1] and 'source-a' in prompts[1]


def meaningful_report():
    return '\n\n'.join(f'Section {i}: The evidence addresses topic dimension {i} with specific observations and uncertainties. Measurements from source A explain mechanism {i} and its relationship to outcome {i + 1}. This analysis tests assumption {i} and identifies implications for investigation {i + 2}.' for i in range(7))


@pytest.mark.parametrize('citations', [[{'label': 'Invented', 'title': 'Fake', 'url': 'https://fake.org/x'}], [{'label': 'A', 'title': 'Evidence', 'url': 'https://example.org/a'}] * 2])
def test_invented_and_duplicate_citations_rejected(monkeypatch, citations):
    monkeypatch.setattr(dr, '_call_model', lambda **k: {'draft': meaningful_report(), 'citations': citations})
    with pytest.raises(GoalBlocked):
        invoke(dr.draft_report, research_values())


@pytest.mark.parametrize('body', ['Too short.', 'Repeated filler without specific evidence. ' * 100])
def test_short_or_repeated_filler_rejected(body):
    with pytest.raises(GoalBlocked):
        dr._apply_length_guardrails(body=body, config=DeepResearchConfig(desired_length_pages=1), topic='Topic')


def test_reference_default_budget_matches_conservative_accounting():
    budget = get_default_budget()
    assert (budget.tokens, budget.ms, budget.calls) == (200000, 300000, 120)


def test_public_writer_absent_coverage_fails_after_configured_attempts(tmp_path, monkeypatch):
    import subprocess
    provider = SuiteProvider()
    agent = writer(tmp_path, provider, attempts=2)
    monkeypatch.setattr(tw.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout='passed', stderr=''))
    result = agent.run()
    assert not result.ok
    meta = result.final.get('tests.progress')['pkg/mod1.py']
    assert meta['attempts'] == 2
    assert not meta['coverage_met']


def test_public_writer_zero_budget_prevents_generation(tmp_path):
    from topology.types import Budget
    provider = SuiteProvider()
    agent = writer(tmp_path, provider)
    agent.agent.engine.budget = Budget(tokens=0, ms=300000, calls=120)
    result = agent.run()
    assert not result.ok
    assert not provider.prompts
    assert not (tmp_path / 'tests/test_mod1.py').exists()


def test_research_notes_reject_invented_source_identity(monkeypatch):
    monkeypatch.setattr(dr, '_call_model', lambda **k: {'key_findings': ['Fact'], 'statistics': [{'metric': 'quantity', 'value': '3', 'source': 'invented-source'}]})
    with pytest.raises(GoalBlocked, match='source'):
        invoke(dr.synthesize_notes, research_values())


def test_custom_research_provider_crosses_policy_boundary(monkeypatch):
    from boot import add_guardrails
    from regions import GuardPII
    from topology.registry import clear_registry
    clear_registry()
    seen = []
    class Provider:
        def generate(self, **request):
            seen.append(request)
            return 'safe reply'
    monkeypatch.setattr(dr, 'resolve_llm_profile', lambda name: (Provider(), {}))
    add_guardrails(GuardPII('block', mode='block'))
    with pytest.raises(GoalBlocked, match='policy'):
        dr._call_model(config=DeepResearchConfig(), stage='draft', profile='injected', prompt='email me at private@example.org')
    assert not seen
    clear_registry()


def test_low_coverage_first_target_cannot_exceed_cap_or_starve_second(tmp_path, monkeypatch):
    import subprocess
    provider = SuiteProvider()
    agent = writer(tmp_path, provider, targets=2, attempts=2)
    executions = []
    def pytest_run(command, **kwargs):
        target = 'mod1' if 'tests/test_mod1.py' in command else 'mod2'
        executions.append(target)
        (tmp_path / tw.COVERAGE_PATH).write_text(json.dumps({'totals': {'percent_covered': 20 if target == 'mod1' else 100}, 'files': {}}))
        return subprocess.CompletedProcess(command, 0, stdout='passed', stderr='')
    monkeypatch.setattr(tw.subprocess, 'run', pytest_run)
    result = agent.run()
    assert not result.ok
    progress = result.final.get('tests.progress')
    assert progress['pkg/mod1.py']['attempts'] == 2
    assert progress['pkg/mod2.py']['status'] == 'passed'
    assert executions == ['mod1', 'mod1', 'mod2']


def test_target_transition_invalidates_old_artifacts():
    entry = {'path': 'pkg/new.py', 'module': 'pkg.new', 'suggested_test_path': 'tests/test_new.py'}
    values = {'tests.todo': [entry], 'tests.progress': {}, 'tests.active.generated': {'identity': {'target': 'pkg/old.py', 'attempt': 1}, 'tests': []}, 'tests.run.result': {'status': 'passed'}}
    result = invoke(tw.select_next_target, values)
    assert result['tests.active.entry']['attempt'] == 1
    for key in ['tests.active.context', 'tests.active.generated', 'tests.write.result', 'tests.run.result', 'tests.active.status']:
        assert key in result and result[key] is None
    assert result['tests.write.requests'] == []


def test_execution_status_retains_result_identity():
    identity = {'target': 'pkg/old.py', 'attempt': 1}
    result = invoke(tw.summarize_test_execution, {'tests.active.entry': {'path': 'pkg/old.py', 'attempt': 1}, 'tests.run.result': {'identity': identity, 'status': 'passed'}})
    assert result['tests.active.status.identity'] == identity


def test_stale_coverage_cannot_certify_new_attempt():
    entry = {'path': 'pkg/mod1.py', 'attempt': 2}
    result = invoke(tw.update_progress_trackers, {'tests.active.entry': entry, 'tests.run.result': {'identity': {'target': entry['path'], 'attempt': 2}, 'status': 'passed'}, 'coverage.report': {'identity': {'target': entry['path'], 'attempt': 1}, 'total': 1.0, 'available': True}})
    assert result == {}


def test_public_two_target_exact_acceptance_second_mod1_fails(tmp_path):
    class Provider(SuiteProvider):
        def generate(self, **request):
            module = 'mod2' if 'pkg.mod2' in request['prompt'] else 'mod1'
            return json.dumps({'tests': [{'path': f'tests/test_{module}.py', 'content': f'from pkg.{module} import value\n\ndef test_value():\n    assert value() == {1 if module == "mod2" else 2}\n'}]})
    agent = writer(tmp_path, Provider(), targets=2, attempts=1)
    (tmp_path / 'pkg/mod2.py').write_text('def value():\n    # prioritize this larger module first\n    return 1\n')
    outside = tmp_path.parent / (tmp_path.name + '-untouched')
    outside.write_text('original')
    result = agent.run()
    assert (tmp_path / 'tests/test_mod1.py').exists()
    assert result.final.get('tests.progress')['pkg/mod1.py']['status'] != 'passed'
    assert result.ok is False
    assert outside.read_text() == 'original'


def test_standalone_repair_rejects_mismatched_context():
    from core.runners.llm_runner import SkipLLM
    values = {'tests.active.entry': {'path': 'pkg/new.py', 'attempt': 2}, 'tests.active.context': {'identity': {'target': 'pkg/old.py', 'attempt': 1}, 'module': 'pkg.old'}, 'tests.active.status': 'failed', 'tests.active.failures': [{'summary': 'old failure'}]}
    with pytest.raises(SkipLLM):
        tw._map_repair_prompt(snap(values))


@pytest.mark.parametrize('capability', ['file', 'http'])
def test_reference_side_effects_cross_registered_guard_boundary(tmp_path, monkeypatch, capability):
    from core.sdk import Agent, goal
    from boot import add_guardrails
    from regions import GuardPII
    from topology.registry import clear_registry
    clear_registry()
    add_guardrails(GuardPII('block', mode='block'))
    @goal(scope=['done'])
    def done(ws):
        return bool(ws.get('done'))
    if capability == 'file':
        (tmp_path / 'tests').mkdir()
        target = tmp_path / 'tests/test_private.py'
        target.write_text('original')
        entry = {'path': 'pkg/mod1.py', 'attempt': 1}
        initial = {'repo.root': str(tmp_path), 'tests.active.entry': entry, 'tests.write.requests': [{'identity': tw._identity(entry), 'path': 'tests/test_private.py', 'content': '# private@example.org'}]}
        result = Agent([tw.write_test_files]).run(initial=initial, goal=done)
        assert target.read_text() == 'original'
    else:
        monkeypatch.setattr(dr, 'fetch_sources', lambda *a, **k: pytest.fail('blocked HTTP action executed'))
        initial = {'research.plan': {}, 'research.queries': ['contact private@example.org'], 'deepresearch.config': DeepResearchConfig().to_state()}
        result = Agent([dr.gather_sources]).run(initial=initial, goal=done)
    assert not result.ok
    assert result.blocker.reason == 'policy_blocked'
    clear_registry()


@pytest.mark.parametrize('invalid', ['directory', 'file_ancestor', 'tests_root_file', 'batch_parent_first', 'batch_child_first'])
def test_whole_batch_preflight_rejects_destination_types_and_ancestors(tmp_path, invalid):
    root = tmp_path / 'repo'
    root.mkdir()
    if invalid == 'tests_root_file':
        (root / 'tests').write_text('tests root is a file')
        first = root / 'tests/test_first.py'
        requests = [{'path': 'tests/test_first.py', 'content': 'replacement'}]
    else:
        (root / 'tests').mkdir()
        first = root / 'tests/test_first.py'
        first.write_text('original')
        requests = [{'path': 'tests/test_first.py', 'content': 'replacement'}]
        if invalid == 'directory':
            (root / 'tests/existing_dir').mkdir()
            requests.append({'path': 'tests/existing_dir', 'content': 'invalid'})
        elif invalid == 'file_ancestor':
            (root / 'tests/ancestor').write_text('ancestor original')
            requests.append({'path': 'tests/ancestor/test_nested.py', 'content': 'invalid'})
        else:
            conflicts = [{'path': 'tests/new_ancestor', 'content': 'ancestor'}, {'path': 'tests/new_ancestor/test_nested.py', 'content': 'descendant'}]
            if invalid == 'batch_child_first':
                conflicts.reverse()
            requests.extend(conflicts)
    with pytest.raises(GoalBlocked, match='invalid_test_write'):
        invoke(tw.write_test_files, {'repo.root': str(root), 'tests.write.requests': requests})
    if invalid == 'tests_root_file':
        assert (root / 'tests').read_text() == 'tests root is a file'
        assert not first.exists()
    else:
        assert first.read_text() == 'original'
        if invalid == 'file_ancestor':
            assert (root / 'tests/ancestor').read_text() == 'ancestor original'
        if invalid.startswith('batch_'):
            assert not (root / 'tests/new_ancestor').exists()


@pytest.mark.parametrize('selection', ['', 'approve'])
def test_required_review_actual_interactive_runner_requires_explicit_selection(monkeypatch, selection):
    import core.runners.human_runner as human_runner
    monkeypatch.setattr(human_runner.sys.stdin, 'isatty', lambda: True)
    monkeypatch.delenv('RANGER_FIELD_STATUS', raising=False)
    monkeypatch.delenv('RANGER_FIELD_COMMENTS', raising=False)
    responses = iter([selection, ''])
    monkeypatch.setattr('builtins.input', lambda *a: next(responses))
    values = research_values(DeepResearchConfig(require_human_feedback=True))
    if not selection:
        with pytest.raises(GoalBlocked, match='human_feedback_required'):
            invoke(dr.human_review, values)
    else:
        result = invoke(dr.human_review, values)
        assert result['human.feedback']['status'] == 'approve'
        integrated = invoke(dr.integrate_feedback, {**values, **result})
        assert integrated['research.final_draft'].startswith(values['research.draft'])


@pytest.mark.parametrize('filler', ['Filler filler. ' * 5000, 'Echo\n' * 5000], ids=['short-sentences', 'short-lines'])
def test_good_substantive_content_plus_short_repeated_filler_is_rejected(filler):
    body = meaningful_report() + '\n\n' + filler
    with pytest.raises(GoalBlocked, match='repeated_content'):
        dr._apply_length_guardrails(body=body, config=DeepResearchConfig(desired_length_pages=1), topic='Topic')
