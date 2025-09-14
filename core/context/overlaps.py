"""Compute overlaps between contexts based on shared resources."""

from typing import Iterable, Tuple, List
from core.context.model import Context


def compute_overlaps(ctxs: Iterable[Context]) -> List[Tuple[str, str]]:
    """
    Compute overlaps between contexts.
    
    V1: Contexts overlap if they share at least one resource tag.
    Returns sorted list of context ID pairs that overlap.
    """
    lst = list(ctxs)
    edges: List[Tuple[str, str]] = []
    
    for i in range(len(lst)):
        for j in range(i + 1, len(lst)):
            ctx_a, ctx_b = lst[i], lst[j]
            
            # Check for shared resources
            if set(ctx_a.resources) & set(ctx_b.resources):
                # Sort IDs for consistent ordering
                a_id, b_id = sorted((ctx_a.id, ctx_b.id))
                edges.append((a_id, b_id))
    
    return edges
