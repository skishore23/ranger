"""Graphviz rendering helpers for Ranger capability graphs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

from core.capability import Capability


class GraphvizUnavailable(RuntimeError):
    """Internal exception raised when Graphviz is missing."""


def _ensure_graphviz():
    try:
        from graphviz import Digraph  # type: ignore
        from graphviz.backend import ExecutableNotFound  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency missing
        raise GraphvizUnavailable(
            "Graphviz support requires installing the optional dependency: pip install ranger[viz]"
        ) from exc
    return Digraph, ExecutableNotFound


def render_capability_graph(
    capabilities: Sequence[Capability],
    output: Path,
    *,
    fmt: str = "png",
    graph_name: str = "ranger_agent",
    rankdir: str = "LR",
    capability_color: str = "#5A67D8",
    state_color: str = "#EDF2F7",
    read_edge_color: str = "#2C5282",
    write_edge_color: str = "#2F855A",
) -> Path:
    """Render a Graphviz graph showing capability ↔ state relationships.

    Args:
        capabilities: Ordered sequence of capabilities to visualise.
        output: Path (without extension) to write the rendered graph to.
        fmt: Graphviz output format (e.g., "png", "svg").
        graph_name: Name for the generated graph.
        rankdir: Graphviz rank direction ("LR", "TB", ...).
        capability_color: Fill colour for capability nodes.
        state_color: Fill colour for state nodes.
        read_edge_color: Colour for edges from state → capability (reads).
        write_edge_color: Colour for edges from capability → state (writes).

    Returns:
        Path to the rendered file produced by Graphviz.
    """

    Digraph, ExecutableNotFound = _ensure_graphviz()

    graph = Digraph(graph_name, format=fmt)
    graph.attr(rankdir=rankdir)

    state_nodes: dict[str, str] = {}

    def state_id(key: str) -> str:
        safe = re.sub(r"[^0-9a-zA-Z]+", "_", key)
        return f"state_{safe}" if safe else f"state_{abs(hash(key))}"

    for cap in capabilities:
        node_id = f"cap_{cap.id}"
        graph.node(
            node_id,
            label=cap.id,
            shape="box",
            style="filled",
            fillcolor=capability_color,
            fontcolor="white",
        )

        for key in sorted(cap.reads or set()):
            sid = state_id(key)
            if sid not in state_nodes:
                graph.node(sid, label=key, shape="ellipse", fillcolor=state_color, style="filled")
                state_nodes[sid] = key
            graph.edge(sid, node_id, color=read_edge_color)

        for key in sorted(cap.writes or set()):
            sid = state_id(key)
            if sid not in state_nodes:
                graph.node(sid, label=key, shape="ellipse", fillcolor=state_color, style="filled")
                state_nodes[sid] = key
            graph.edge(node_id, sid, color=write_edge_color)

    try:
        return Path(graph.render(str(output), cleanup=True))
    except ExecutableNotFound as exc:  # pragma: no cover - environment specific
        raise GraphvizUnavailable(
            "Graphviz executable not found. Install Graphviz from https://graphviz.org/download/."
        ) from exc


__all__ = ["render_capability_graph", "GraphvizUnavailable"]
