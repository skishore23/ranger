from __future__ import annotations

from pathlib import Path

from agents.deep_research.agent import DeepResearchAgent
from agents.deep_research.types import DeepResearchConfig


def _offline_agent(tmp_path, monkeypatch, *, require_review=False, memory_kwargs=None):
    import json
    from agents.deep_research import capabilities
    from core.llm.provider import register_llm_profile
    from tests.test_reference_agent_reliability import meaningful_report
    config = DeepResearchConfig(desired_length_pages=1, min_citations=1,
                                require_human_feedback=require_review,
                                output_path=tmp_path / 'report.md')
    agent = DeepResearchAgent(repo_root=tmp_path, topic="Quantum computing impact", config=config, memory_kwargs=memory_kwargs)
    class Provider:
        def generate(self, **request):
            schema = request.get('schema') or {}
            properties = schema.get('properties', {})
            if 'queries' in properties:
                result = {'executive_summary': 'Investigate quantum computing', 'research_questions': ['What changed?'], 'sections': [{'title': 'Analysis'}], 'queries': ['quantum']}
            elif 'key_findings' in properties:
                result = {'key_findings': ['Quantum computing evidence'], 'statistics': [{'metric': 'observations', 'value': '1', 'source': 'https://example.org/a'}]}
            else:
                result = {'draft': 'Quantum computing impact\n\n' + meaningful_report(), 'citations': [{'label': 'A', 'title': 'Evidence', 'url': 'https://example.org/a'}]}
            return json.dumps(result)
    for stage in ['plan', 'notes', 'draft']:
        register_llm_profile('deepresearch.' + stage, provider=Provider(), defaults={'model': 'stub', 'max_tokens': 7000})
    monkeypatch.setattr(capabilities, 'get_api_key', lambda: 'offline')
    monkeypatch.setattr(capabilities, 'fetch_sources', lambda *a, **k: [{'title': 'Evidence', 'url': 'https://example.org/a', 'snippet': 'Quantum computing evidence', 'content': 'A retrieved finding'}])
    return agent


def test_deep_research_agent_offline(tmp_path: Path, monkeypatch) -> None:
    agent = _offline_agent(tmp_path, monkeypatch)
    result = agent.run(max_steps=80)
    assert result.ok, result.blocker
    assert 'Quantum computing' in result.final.get('research.report')
    assert result.final.get('research.summary')['citations'] == 1
    assert (tmp_path / 'report.md').exists()
    assert agent.scenario_report(run_id=result.run_id).ok


def test_required_review_public_run_waits_offline(tmp_path, monkeypatch):
    agent = _offline_agent(tmp_path, monkeypatch, require_review=True)
    result = agent.run(max_steps=80)
    assert not result.ok
    assert result.blocker.reason == 'human_feedback_required'
    assert not result.final.exists('research.report')
    assert not (tmp_path / 'report.md').exists()
    assert not agent.scenario_report(run_id=result.run_id).ok


def test_deep_research_visualize_stub(tmp_path: Path) -> None:
    agent = DeepResearchAgent(repo_root=tmp_path, topic="AI governance")
    try:
        import graphviz  # type: ignore
    except ImportError:
        return

    output = tmp_path / "deep_viz"
    try:
        path = agent.visualize(output, fmt="svg")
    except RuntimeError as exc:
        if "Graphviz executable" in str(exc):
            return
        raise
    assert path.exists()


def test_deep_research_scenario_helpers(tmp_path: Path) -> None:
    agent = DeepResearchAgent(repo_root=tmp_path, topic="Biosecurity strategy")
    report = agent.scenario_report()
    assert not report.ok
    assert agent.scenario_timeline() == []


def test_required_review_public_interactive_blank_cannot_publish(tmp_path, monkeypatch):
    import core.runners.human_runner as human_runner
    monkeypatch.setattr(human_runner.sys.stdin, 'isatty', lambda: True)
    monkeypatch.delenv('RANGER_FIELD_STATUS', raising=False)
    monkeypatch.delenv('RANGER_FIELD_COMMENTS', raising=False)
    responses = iter(['', ''])
    monkeypatch.setattr('builtins.input', lambda *a: next(responses))
    agent = _offline_agent(tmp_path, monkeypatch, require_review=True)
    result = agent.run()
    assert not result.ok
    assert result.blocker.reason == 'human_feedback_required'
    assert not result.final.exists('human.feedback')
    assert not result.final.exists('research.report')
    assert not (tmp_path / 'report.md').exists()


def test_required_review_public_interactive_explicit_approve_publishes(tmp_path, monkeypatch):
    import core.runners.human_runner as human_runner
    monkeypatch.setattr(human_runner.sys.stdin, 'isatty', lambda: True)
    monkeypatch.delenv('RANGER_FIELD_STATUS', raising=False)
    monkeypatch.delenv('RANGER_FIELD_COMMENTS', raising=False)
    responses = iter(['approve', ''])
    monkeypatch.setattr('builtins.input', lambda *a: next(responses))
    agent = _offline_agent(tmp_path, monkeypatch, require_review=True)
    result = agent.run()
    assert result.ok, result.blocker
    assert result.final.get('human.feedback')['status'] == 'approve'
    assert result.final.exists('research.report')
    assert (tmp_path / 'report.md').exists()
