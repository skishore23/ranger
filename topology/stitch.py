"""Summarisation and reconciliation helpers for context construction."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .types import Atom, Path, Region


def summarize_regions(regions: Iterable[Region], atoms: Iterable[Atom], goal: Dict[str, Any]) -> List[Atom]:
    """Ask each region to summarise its relevant atoms."""
    atom_list = list(atoms)
    index: Dict[str, List[Atom]] = {}
    for atom in atom_list:
        source = atom.facets.get("source")
        index.setdefault(source, []).append(atom)

    summaries: List[Atom] = []
    for region in regions:
        region_atoms = index.get(region.key) or atom_list
        try:
            summary = region.summarize(region_atoms, goal)
        except Exception as exc:  # noqa: BLE001 - surface actionable failure
            raise RuntimeError(f"Region {region.key} failed to summarise atoms") from exc
        if summary is None:
            continue
        summaries.append(summary)
    return summaries


def reconcile_overlaps(summaries: Iterable[Atom], path: Path, goal: Dict[str, Any]) -> List[Atom]:
    """Merge summaries per region using each region's reconcile logic when available."""

    region_lookup: Dict[str, Region] = {
        region.key: region
        for region in (
            list(path.memory_like)
            + list(path.guards)
            + list(path.models)
            + list(path.tools)
        )
    }

    merged: Dict[str, Atom] = {}
    order: List[str] = []

    for atom in summaries:
        source = atom.facets.get("source") or atom.schema or atom.id
        existing = merged.get(source)
        if existing is None:
            merged[source] = atom
            order.append(source)
            continue

        region: Optional[Region] = region_lookup.get(source)
        if region is not None:
            ok, preferred, _reason = region.reconcile(existing, atom, goal)
            if ok and preferred is not None:
                merged[source] = preferred
                continue

        newer = atom if (atom.facets.get("ts", 0) >= existing.facets.get("ts", 0)) else existing
        merged[source] = newer

    return [merged[key] for key in order]


def repair_context(atoms: Iterable[Atom], validation_result: Dict[str, Any], guard: Region) -> List[Atom]:
    """Allow guard regions to repair/redact atoms when violations occur."""

    findings = validation_result.get("findings", [])
    if hasattr(guard, "redact_atoms") and findings:
        try:
            return list(guard.redact_atoms(atoms, findings))  # type: ignore[attr-defined]
        except Exception:
            return list(atoms)
    return list(atoms)


__all__ = [
    "summarize_regions",
    "reconcile_overlaps",
    "repair_context",
]
