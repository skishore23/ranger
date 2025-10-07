"""Lens helpers for tailoring atoms before tool execution."""

from __future__ import annotations

from typing import Iterable, List

from .types import Atom


def args_only(atoms: Iterable[Atom]) -> List[Atom]:
    """Placeholder lens returning the atoms unchanged."""
    return list(atoms)


def redact(atoms: Iterable[Atom], *, fields: Iterable[str] | None = None) -> List[Atom]:
    """Placeholder redaction lens that currently performs no masking."""
    return list(atoms)


def apply_modality_caps(atoms: Iterable[Atom], caps: dict[str, int]) -> List[Atom]:
    """Enforce basic per-modality caps by slicing the sequence."""
    counts: dict[str, int] = {}
    output: List[Atom] = []
    for atom in atoms:
        cap = caps.get(atom.modality)
        if cap is not None and counts.get(atom.modality, 0) >= cap:
            continue
        output.append(atom)
        counts[atom.modality] = counts.get(atom.modality, 0) + 1
    return output


__all__ = ["args_only", "redact", "apply_modality_caps"]
