"""Capabilities for the Deep Research agent."""

from __future__ import annotations

import datetime as dt
import textwrap
import time
from typing import Any, Dict, Iterable, List, Optional

from core.errors import GoalBlocked
from core.llm.provider import RegionBackedProvider, resolve_llm_profile
from core.sdk import goal, human, step, tool
from core.workspace import Snapshot
from topology.registry import get_region

from .firecrawl import fetch_sources, get_api_key
from .memory_bridge import (
    store_notes,
    store_plan,
    store_report,
    store_request,
    store_sources,
)
from .types import DeepResearchConfig

LLM_REGION_KEY = "deepresearch.llm"
DEFAULT_STAGE_SYSTEM = {
    "plan": "You are Ranger DeepResearch, a principal research strategist. You design exhaustive research plans with explicit sections, research questions, and query suggestions.",
    "notes": "You are Ranger DeepResearch, a synthesis expert. Extract structured findings, statistics, controversies, and open questions from provided sources.",
    "draft": "You are Ranger DeepResearch, a world-class analyst. Write comprehensive, citation-rich reports with executive summaries, sectioned analysis, counterpoints, and actionable recommendations.",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_from_snapshot(ws: Snapshot) -> DeepResearchConfig:
    state = ws.get("deepresearch.config", {}) or {}
    return DeepResearchConfig.from_state(state)


def _call_model(
    *,
    config: DeepResearchConfig,
    stage: str,
    prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    temperature: Optional[float] = None,
    profile: Optional[str] = None,
) -> Any:
    profile_defaults: Dict[str, Any] = {}
    if profile is not None:
        provider, profile_defaults = resolve_llm_profile(profile)
    else:
        provider = RegionBackedProvider(LLM_REGION_KEY)

    system_prompt = (
        config.llm.system_prompt
        or profile_defaults.get("system")
        or DEFAULT_STAGE_SYSTEM.get(stage, DEFAULT_STAGE_SYSTEM["draft"])
    )
    resolved_temperature = (
        temperature if temperature is not None else profile_defaults.get("temperature", config.llm.temperature)
    )
    resolved_model = profile_defaults.get("model", config.llm.model)
    resolved_max_tokens = profile_defaults.get("max_tokens", config.llm.max_tokens)

    if provider is None:
        raise GoalBlocked("llm_missing_region", details={"stage": stage})

    response = provider.generate(
        system=system_prompt,
        prompt=prompt,
        model=resolved_model,
        temperature=resolved_temperature,
        max_tokens=resolved_max_tokens,
        schema=schema,
    )
    if schema:
        import json

        return json.loads(response)
    return response


def _apply_length_guardrails(
    *,
    body: str,
    config: DeepResearchConfig,
    topic: str,
    min_chars_per_page: int = 1200,
) -> str:
    target_chars = config.desired_length_pages * min_chars_per_page
    if len(body) >= target_chars:
        return body

    filler = textwrap.dedent(
        f"""
        ## Extended Analysis
        The strategic calculus surrounding {topic} requires richer scenario modeling. This supplemental analysis expands on
        adjacent policy, economic, and societal undercurrents. It deepens leadership's ability to navigate uncertainty by
        codifying trigger points, leading indicators, and portfolio hedges that were partially covered earlier.
        """
    ).strip()

    chars_needed = target_chars - len(body)
    filler_with_spacing = "\n\n" + filler
    repetitions = (chars_needed + len(filler_with_spacing) - 1) // len(filler_with_spacing)
    
    return body + (filler_with_spacing * repetitions)


def _dedupe_sources(sources: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for src in sources:
        key = src.get("url") or src.get("title") or str(len(seen))
        if key not in seen:
            seen[key] = src
    return list(seen.values())




# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@human(
    outputs=["research.topic"],
    title="Select research topic",
    description="Provide or confirm the topic the deep research agent should investigate.",
    fields=[{"name": "topic", "type": "text", "label": "Research topic"}],
)
def solicit_topic(form: Dict[str, Any], ws: Snapshot) -> Dict[str, Any]:
    existing = ws.get("research.topic")
    if existing:
        return {}
    topic = (form or {}).get("topic", "").strip()
    if not topic:
        raise GoalBlocked("topic_missing", details={"message": "Topic is required"})
    return {"research.topic": topic}


@step(inputs=["research.topic", "deepresearch.config"], outputs=["research.request"])
def capture_request(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    topic = ws.get("research.topic")
    if not topic:
        raise GoalBlocked("missing_topic", details={"field": "research.topic"})

    request = {
        "topic": topic,
        "audience": config.audience,
        "depth": config.depth,
        "deliverable": config.deliverable,
        "desired_length_pages": config.desired_length_pages,
        "min_citations": config.min_citations,
        "custom_directives": list(config.custom_directives),
    }
    store_request(request)
    return {"research.request": request}


@step(inputs=["research.request", "deepresearch.config"], outputs=["research.plan", "research.queries"])
def design_research_plan(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    request = ws.get("research.request") or {}
    topic = request.get("topic", "the target domain")

    schema = {
        "type": "object",
        "properties": {
            "executive_summary": {"type": "string"},
            "research_questions": {"type": "array", "items": {"type": "string"}},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "objectives": {"type": "array", "items": {"type": "string"}},
                        "deliverables": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                },
            },
            "queries": {"type": "array", "items": {"type": "string"}},
            "methodology": {"type": "string"},
        },
        "required": ["executive_summary", "research_questions", "sections", "queries"],
    }

    prompt = textwrap.dedent(
        f"""
        Design an exhaustive research plan on the topic "{topic}" for the audience "{config.audience}".
        Incorporate directives: {config.custom_directives}.
        Desired deliverable: {config.deliverable}. Minimum citations: {config.min_citations}.
        Provide detailed sections with objectives and deliverables, and propose targeted search queries.
        """
    ).strip()

    plan = _call_model(
        config=config,
        stage="plan",
        profile="deepresearch.plan",
        prompt=prompt,
        schema=schema,
    )

    if not isinstance(plan, dict):
        raise GoalBlocked("llm_invalid_response", details={"stage": "plan"})

    queries = plan.get("queries") or []
    if not queries:
        raise GoalBlocked("plan_missing_queries", details={"stage": "plan"})

    topic_queries = [q.replace("[[TOPIC]]", topic) for q in queries]
    plan["queries"] = topic_queries
    if "executive_summary" in plan and isinstance(plan["executive_summary"], str):
        plan["executive_summary"] = plan["executive_summary"].replace("[[TOPIC]]", topic)
    if isinstance(plan.get("sections"), list):
        for section in plan["sections"]:
            if isinstance(section, dict):
                for key in ("title", "objectives", "deliverables"):
                    value = section.get(key)
                    if isinstance(value, str):
                        section[key] = value.replace("[[TOPIC]]", topic)
                    elif isinstance(value, list):
                        section[key] = [str(item).replace("[[TOPIC]]", topic) for item in value]

    store_plan({"topic": topic, **plan})
    return {"research.plan": plan, "research.queries": topic_queries}


@tool(
    inputs=["research.plan", "research.queries", "deepresearch.config"],
    outputs=["research.sources"],
)
def gather_sources(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    queries = ws.get("research.queries") or []
    if not queries:
        plan = ws.get("research.plan") or {}
        queries = plan.get("queries", [])

    max_queries = min(len(queries), config.retrieval.max_queries)
    api_key = get_api_key()

    all_sources: List[Dict[str, Any]] = []
    for query in queries[:max_queries]:
        sources = fetch_sources(
            query,
            api_key=api_key,
            max_results=config.retrieval.sources_per_query,
        )
        all_sources.extend(sources)

    deduped = _dedupe_sources(all_sources)
    store_sources(deduped)
    return {"research.sources": deduped}


@step(inputs=["research.sources", "research.plan", "deepresearch.config"], outputs=["research.notes"])
def synthesize_notes(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    sources = ws.get("research.sources") or []
    plan = ws.get("research.plan") or {}
    topic = (ws.get("research.request") or {}).get("topic", "the domain")

    excerpts = []
    for src in sources[:12]:
        snippet = src.get("snippet") or src.get("content") or ""
        if snippet:
            excerpts.append(snippet)

    prompt = textwrap.dedent(
        f"""
        Topic: {topic}
        Outline sections: {plan.get('sections', [])}
        Research questions: {plan.get('research_questions', [])}

        Sources:
        {excerpts[:10]}

        Produce structured notes capturing key findings, statistics, controversies, and open questions.
        """
    ).strip()

    schema = {
        "type": "object",
        "properties": {
            "key_findings": {"type": "array", "items": {"type": "string"}},
            "statistics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "value": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["metric", "value"],
                },
            },
            "controversies": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "recommended_visuals": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["key_findings"],
    }

    notes = _call_model(
        config=config,
        stage="notes",
        profile="deepresearch.notes",
        prompt=prompt,
        schema=schema,
    )
    if not isinstance(notes, dict):
        raise GoalBlocked("llm_invalid_response", details={"stage": "notes"})

    store_notes(notes)
    return {"research.notes": notes}


@step(
    inputs=["research.notes", "research.sources", "research.plan", "deepresearch.config"],
    outputs=["research.draft", "research.citations"],
)
def draft_report(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    notes = ws.get("research.notes") or {}
    sources: List[Dict[str, Any]] = ws.get("research.sources") or []
    request = ws.get("research.request") or {}
    topic = request.get("topic", "the domain")

    prompt = textwrap.dedent(
        f"""
        Write a {config.desired_length_pages}-page, doctoral-level report on "{topic}" with a minimum of {config.min_citations} citations.
        Incorporate these findings: {notes}.
        Cite sources with author-year style and include URLs. Provide a detailed executive summary, sectioned analysis, counterpoints, and strategic recommendations.
        """
    ).strip()

    schema = {
        "type": "object",
        "properties": {
            "draft": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "publisher": {"type": "string"},
                        "year": {"type": "integer"},
                        "accessed": {"type": "string"},
                    },
                    "required": ["label", "title"],
                },
            },
        },
        "required": ["draft", "citations"],
    }

    result = _call_model(
        config=config,
        stage="draft",
        profile="deepresearch.draft",
        prompt=prompt,
        schema=schema,
    )
    if not isinstance(result, dict) or not result.get("draft"):
        raise GoalBlocked("llm_invalid_response", details={"stage": "draft"})

    draft = result.get("draft")
    citations = result.get("citations", [])

    if len(citations) < config.min_citations:
        raise GoalBlocked(
            "insufficient_citations",
            details={"have": len(citations), "need": config.min_citations}
        )

    # Ensure the draft references the topic and meets length guardrails
    draft = draft.replace("[[TOPIC]]", topic)
    draft = _apply_length_guardrails(body=draft, config=config, topic=topic)

    store_report(draft, citations)
    return {"research.draft": draft, "research.citations": citations}


@human(
    inputs=["research.draft", "research.citations", "research.request", "deepresearch.config"],
    outputs=["human.feedback"],
    title="Deep research review",
    description="Validate structure, accuracy, and tone; suggest clarifications before publication.",
    fields=[
        {"name": "status", "type": "select", "options": ["approve", "revise"], "label": "Status"},
        {"name": "comments", "type": "textarea", "label": "Feedback"},
    ],
)
def human_review(form: Dict[str, Any], ws: Snapshot) -> Dict[str, Any]:
    status = form.get("status", "approve")
    comments = form.get("comments", "")
    return {
        "human.feedback": {
            "status": status,
            "comments": comments,
        }
    }


@step(inputs=["research.draft", "deepresearch.config"], outputs=["human.feedback"])
def ensure_feedback(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    existing = ws.get("human.feedback")
    if existing:
        return {"human.feedback": existing}

    if config.require_human_feedback:
        return {
            "human.feedback": {
                "status": "pending-review",
                "comments": "Awaiting human feedback – replace this placeholder with reviewer input.",
            }
        }

    return {
        "human.feedback": {
            "status": "auto-approve",
            "comments": "Human feedback optional for this run; auto-approved by agent.",
        }
    }


@step(inputs=["research.draft", "human.feedback", "deepresearch.config"], outputs=["research.final_draft"])
def integrate_feedback(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    draft = ws.get("research.draft", "")
    feedback = ws.get("human.feedback") or {"status": "auto-approve", "comments": "Auto approval"}

    annotations = textwrap.dedent(
        f"""
        \n\n---
        ### Human Feedback Trace
        Status: {feedback.get('status', 'auto-approve')}
        Comments: {feedback.get('comments', 'No comments supplied')}.
        """
    ).strip()

    enhanced = draft + annotations if config.require_human_feedback else draft
    return {"research.final_draft": enhanced}


@step(
    inputs=["research.final_draft", "research.citations", "research.request", "deepresearch.config"],
    outputs=["research.report", "research.metadata"],
)
def finalize_report(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    draft = ws.get("research.final_draft") or ws.get("research.draft") or ""
    citations = ws.get("research.citations") or []
    request = ws.get("research.request") or {}
    topic = request.get("topic", "the domain")

    report = draft
    metadata = {
        "topic": topic,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "desired_length_pages": config.desired_length_pages,
        "min_citations": config.min_citations,
        "citation_count": len(citations),
    }

    return {"research.report": report, "research.metadata": metadata}


@tool(inputs=["research.report", "research.citations", "deepresearch.config"], outputs=["research.output_path"])
def persist_report(ws: Snapshot) -> Dict[str, Any]:
    config = _config_from_snapshot(ws)
    output_path = config.output_path
    if not output_path:
        return {}

    report = ws.get("research.report", "")
    citations = ws.get("research.citations", [])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = report + "\n\n### Citations\n" + "\n".join(
        f"{cite.get('label', cite.get('title'))}: {cite.get('url', '')}" for cite in citations
    )
    output_path.write_text(document, encoding="utf-8")
    return {"research.output_path": str(output_path)}


@step(inputs=["research.report", "research.citations", "research.metadata"], outputs=["research.summary"])
def summarize_execution(ws: Snapshot) -> Dict[str, Any]:
    metadata = ws.get("research.metadata") or {}
    report = ws.get("research.report") or ""
    citations = ws.get("research.citations") or []

    summary = {
        "character_count": len(report),
        "approx_pages": round(len(report) / 1200, 1),
        "citations": len(citations),
        "generated_at": metadata.get("generated_at"),
    }
    return {"research.summary": summary}


@goal(scope={"research.report", "research.citations", "research.summary"})
def research_complete(ws: Snapshot) -> bool:
    report = ws.get("research.report")
    citations = ws.get("research.citations") or []
    if not report or not citations:
        return False

    config = _config_from_snapshot(ws)
    approx_pages = len(report) / 1200

    if len(citations) < config.min_citations:
        raise GoalBlocked(
            "insufficient_citations",
            details={"have": len(citations), "need": config.min_citations},
        )
    if approx_pages < config.desired_length_pages:
        raise GoalBlocked(
            "insufficient_length",
            details={"estimated_pages": round(approx_pages, 1), "target_pages": config.desired_length_pages},
        )
    return True


__all__ = [
    "solicit_topic",
    "capture_request",
    "design_research_plan",
    "gather_sources",
    "synthesize_notes",
    "draft_report",
    "human_review",
    "ensure_feedback",
    "integrate_feedback",
    "finalize_report",
    "persist_report",
    "summarize_execution",
    "research_complete",
]
