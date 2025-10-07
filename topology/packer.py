"""Utility for selecting atoms under a budget constraint."""

from __future__ import annotations

import json
import math
from typing import Dict, Iterable, List, Sequence, Tuple

from .types import Atom, Budget, ContextWindow


def _approx_tokens(atom: Atom) -> int:
    """Rudimentary token estimate based on string content length."""

    content = atom.content
    if isinstance(content, (dict, list, tuple)):
        try:
            content = json.dumps(content, sort_keys=True, default=str)
        except TypeError:
            content = str(content)
    elif not isinstance(content, str):
        content = str(content)

    if not content:
        return 1

    word_tokens = len(content.split())
    char_tokens = math.ceil(len(content) / 4)
    return max(1, word_tokens, char_tokens)


def _domain_affinity(atom: Atom, goal: Dict[str, object]) -> float:
    domain = goal.get("domain")
    if not domain:
        return 0.0
    return 1.0 if atom.facets.get("domain") == domain else 0.0


def _compute_utility(atom: Atom, reference_ts: int, goal: Dict[str, object]) -> float:
    """Blend trust, recency, domain, and provenance into a score."""

    facets = atom.facets or {}
    trust = float(facets.get("trust", 0.5))
    ts = int(facets.get("ts", 0))
    if reference_ts <= 0:
        recency = 0.5
    else:
        age = max(reference_ts - ts, 0)
        recency = max(0.0, min(1.0, 1.0 - age / max(reference_ts, 1)))
    modality_bonus = 0.1 if atom.modality in {"code", "json"} else 0.0
    domain_match = _domain_affinity(atom, goal)
    provenance = atom.provenance or {}
    depth = len(provenance.get("parents", []))
    centrality = 1.0 / (1 + depth)
    return round(
        trust * 0.45 + recency * 0.25 + domain_match * 0.2 + centrality * 0.1 + modality_bonus,
        6,
    )


def _within_caps(modality_counts: Dict[str, int], caps: Dict[str, int] | None, atom: Atom) -> bool:
    if not caps:
        return True
    cap = caps.get(atom.modality)
    if cap is None:
        return True
    return modality_counts.get(atom.modality, 0) < cap


def _score_atoms(atoms: Sequence[Atom], goal: Dict[str, object]) -> List[Tuple[Atom, float, int]]:
    reference_ts = max((int(atom.facets.get("ts", 0)) for atom in atoms), default=0)
    scored: List[Tuple[Atom, float, int]] = []
    for atom in atoms:
        scored.append((atom, _compute_utility(atom, reference_ts, goal), _approx_tokens(atom)))
    scored.sort(key=lambda item: (item[1], int(item[0].facets.get("ts", 0))), reverse=True)
    return scored


def pack_context(atoms: Iterable[Atom], budget: Budget, goal: Dict[str, object]) -> ContextWindow:
    """Return a context window that respects budget and modality limits."""

    atom_list = list(atoms)
    if not atom_list or budget.tokens <= 0:
        empty_budget = Budget(tokens=0, ms=0, calls=0, by_modality=None)
        return ContextWindow(atoms=[], budget_used=empty_budget, regions_used=[], utility_score=0.0)

    scored = _score_atoms(atom_list, goal)
    selected: List[Atom] = []
    modality_counts: Dict[str, int] = {}
    used_tokens = 0
    utility_score = 0.0
    token_cap = max(budget.tokens, 0)

    for atom, score, tokens in scored:
        if tokens > token_cap:
            continue
        if used_tokens + tokens > token_cap:
            continue
        if not _within_caps(modality_counts, budget.by_modality, atom):
            continue

        selected.append(atom)
        used_tokens += tokens
        modality_counts[atom.modality] = modality_counts.get(atom.modality, 0) + 1
        utility_score += score

    ms_used = min(budget.ms, len(selected) * 10) if selected else 0
    calls_used = 1 if selected else 0
    budget_used = Budget(tokens=used_tokens, ms=ms_used, calls=calls_used, by_modality=budget.by_modality)

    regions_used = sorted(
        {atom.facets.get("source") for atom in selected if atom.facets.get("source")}
    )

    return ContextWindow(
        atoms=selected,
        budget_used=budget_used,
        regions_used=regions_used,
        utility_score=utility_score,
    )


__all__ = ["pack_context"]
