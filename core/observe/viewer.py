"""Visualization of topology regions and execution paths (enhanced)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx

from core.context.model import Context
from core.state.types import State


Pos = Dict[str, Tuple[float, float]]
Edge = Tuple[str, str]


@dataclass(frozen=True)
class RenderOpts:
    outpath: str = "region_graph.png"
    seed: int = 42
    pin_start: Optional[str] = None
    pin_goal: Optional[str] = None
    pos_cache: str = ".viz_pos.json"


def _dedupe_edges(edges: Iterable[Edge]) -> List[Edge]:
    uniq: Set[Edge] = set()
    for a, b in edges:
        if a == b:
            continue
        u, v = (a, b) if a < b else (b, a)
        uniq.add((u, v))
    return sorted(uniq)


def _find_triangles(G: nx.Graph) -> List[Tuple[str, str, str]]:
    """Cliques of size 3 -> faint filled triangles (2-simplices)."""
    tris: Set[Tuple[str, str, str]] = set()
    for clique in nx.enumerate_all_cliques(G):
        if len(clique) == 3:
            a, b, c = sorted(clique)
            tris.add((a, b, c))
    return sorted(tris)


def _save_pos(pos: Pos, path: Path) -> None:
    """Save layout positions to JSON cache."""
    path.write_text(json.dumps({k: [float(x), float(y)] for k, (x, y) in pos.items()}))


def _load_pos(path: Path) -> Optional[Pos]:
    """Load layout positions from JSON cache."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
    except Exception:
        return None


def _stable_layout(G: nx.Graph, cache_path: Path, seed: int, pin_start: Optional[str], pin_goal: Optional[str]) -> Pos:
    """Get stable layout with persistent caching."""
    pos = _load_pos(cache_path)
    if pos is not None and set(pos.keys()) == set(G.nodes):  # cache still valid
        return pos
    
    # Generate new layout
    pos = nx.kamada_kawai_layout(G, weight=None)  # clean for small graphs
    pos = {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    
    # Apply pins
    if pin_start and pin_start in pos:
        pos[pin_start] = (-0.95, 0.90)
    if pin_goal and pin_goal in pos:
        pos[pin_goal] = (0.95, -0.85)
    
    # Save to cache
    _save_pos(pos, cache_path)
    return pos


def render_regions_and_path(
    contexts: Sequence[Context],
    overlaps: Sequence[Edge],
    path: Sequence[str],
    *,
    ready: Optional[Set[str]] = None,
    current: Optional[str] = None,
    edge_visits: Optional[Mapping[Edge, int]] = None,
    node_visits: Optional[Mapping[str, int]] = None,
    guard_nodes: Optional[Set[str]] = None,
    opts: RenderOpts = RenderOpts(),
) -> None:
    """
    Render topology graph with regions and execution path.

    Visual cues:
      - Nodes: visited (warm), inactive (cool), current (bold border), ready (halo)
      - Edges: background dashed; path edges thick with arrowheads; thickness ~ visit count
      - Triangles: faint fill for triple overlaps
      - Step indices under path nodes
      - Guard nodes: dashed red rings
      - Node dwell time: ring thickness
    """
    ready = ready or set()
    edge_visits = edge_visits or {}
    node_visits = node_visits or {}
    guard_nodes = guard_nodes or set()

    # Graph
    G: nx.Graph = nx.Graph()
    for ctx in contexts:
        G.add_node(ctx.id, label=ctx.label)
    for a, b in _dedupe_edges(overlaps):
        if a in G and b in G:
            G.add_edge(a, b)

    # Positions (with persistent cache)
    cache_path = Path(opts.pos_cache)
    pos = _stable_layout(G, cache_path, opts.seed, opts.pin_start, opts.pin_goal)

    # Prep sets
    path_nodes: Set[str] = set(path)
    path_pairs: List[Edge] = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        if u in G and v in G:
            path_pairs.append((u, v))
    path_pairs_u = {(min(u, v), max(u, v)) for (u, v) in path_pairs}

    # Plot
    fig, ax = plt.subplots(figsize=(10, 7), dpi=140)
    ax.set_axis_off()

    # Triangles (2-simplices)
    for a, b, c in _find_triangles(G):
        pts = [pos[a], pos[b], pos[c]]
        poly = mpatches.Polygon(pts, closed=True, linewidth=0.6, alpha=0.08)
        ax.add_patch(poly)

    # Background edges
    for (u, v) in G.edges():
        if (min(u, v), max(u, v)) in path_pairs_u:
            continue
        ax.plot(
            [pos[u][0], pos[v][0]],
            [pos[u][1], pos[v][1]],
            linewidth=1.0,
            alpha=0.35,
            linestyle="dashed",
        )

    # Path edges (arrowed, thickness by visits)
    for (u, v) in path_pairs:
        uv_u = (u, v) if u < v else (v, u)
        w = 2.2 + 0.8 * math.log1p(edge_visits.get(uv_u, 1))  # logarithmic scaling
        ax.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]], linewidth=w)
        ax.annotate(
            "", xy=pos[v], xytext=pos[u], arrowprops=dict(arrowstyle="->", lw=w)
        )

    # Nodes with halos/rings
    for n in G.nodes():
        x, y = pos[n]
        is_visited = n in path_nodes
        base_size = 140 if is_visited else 80
        
        # Ring thickness based on dwell time
        ring = 1.0 + 0.6 * math.log1p(node_visits.get(n, 0))
        border_lw = 2.6 if n == current else ring
        
        # Edge color and style for guard nodes
        edge_color = "black" if n == current else ("red" if n in guard_nodes else "gray")
        ls = (0, (4, 2)) if n in guard_nodes else "solid"  # dashed for guard nodes
        
        face_alpha = 0.95 if is_visited else 0.25

        ax.scatter(
            [x],
            [y],
            s=base_size,
            linewidth=border_lw,
            edgecolors=edge_color,
            linestyle=ls,
            alpha=face_alpha,
        )

        # Ready halo
        if n in ready:
            halo = mpatches.Circle((x, y), 0.055, fill=False, lw=2.0, alpha=0.8)
            ax.add_patch(halo)

        # Safe node label access
        node_data = G.nodes.get(n, {})
        if node_data is None:
            node_data = {}
        label = node_data.get("label", n) if isinstance(node_data, dict) else n
        ax.text(x, y + 0.03, label, ha="center", fontsize=9)

    # Step indices
    for i, n in enumerate(path):
        if n in pos:
            ax.text(pos[n][0], pos[n][1] - 0.035, f"{i}", ha="center", fontsize=8)

    # Legend / scoreboard hint
    ax.text(
        0.01,
        0.02,
        "• visited   o inactive   * current   O halo=ready   -> path (thicker=more visits)",
        transform=ax.transAxes,
        fontsize=8,
    )

    plt.tight_layout()
    plt.savefig(opts.outpath, bbox_inches="tight")
    plt.close(fig)


def render_png(
    state: State,
    contexts: Sequence[Context],
    output_path: Path,
    *,
    path: Optional[List[str]] = None,
    ready_ids: Optional[Set[str]] = None,
    edge_visits: Optional[Mapping[Edge, int]] = None,
    node_visits: Optional[Mapping[str, int]] = None,
    guard_nodes: Optional[Set[str]] = None,
    pin_start: Optional[str] = None,
    pin_goal: Optional[str] = None,
) -> None:
    """
    Convenience wrapper that derives:
      - active (ready) contexts from predicates,
      - overlaps from shared resources,
      - a path: `state.meta.get('path', [])` extended with current if present.
    """
    # Active contexts (ready)
    active = [c for c in contexts if c.is_valid(state)]
    ready_ids = ready_ids or {c.id for c in active}

    # Path (prefer an existing path in state.meta)
    meta_path = []
    try:
        if state.meta is not None:
            meta_path = list(state.meta.get("path", []))  # type: ignore[assignment]
    except Exception:
        meta_path = []
    current = active[0].id if active else None
    if path is None:
        path = meta_path.copy()
        if current and (not path or path[-1] != current):
            path.append(current)

    # Overlaps (shared resources)
    overlaps: List[Edge] = []
    for i, c1 in enumerate(contexts):
        for c2 in contexts[i + 1 :]:
            if set(c1.resources) & set(c2.resources):
                overlaps.append((c1.id, c2.id))

    render_regions_and_path(
        contexts,
        overlaps,
        path,
        ready=ready_ids,
        current=current,
        edge_visits=edge_visits,
        node_visits=node_visits,
        guard_nodes=guard_nodes,
        opts=RenderOpts(outpath=str(output_path), pin_start=pin_start, pin_goal=pin_goal),
    )
