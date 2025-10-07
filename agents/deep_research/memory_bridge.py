"""Helpers for emitting research artifacts into the topology memory region."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Iterable, List

from topology.registry import get_region
from topology.types import Atom

MEMORY_KEY = "deepresearch.memory"
_DOMAIN = "deepresearch"


def _now() -> int:
    return int(time.time() * 1000)


def _hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def store_request(request: Dict[str, Any]) -> None:
    region = get_region(MEMORY_KEY)
    if region is None:
        return
    atom = Atom(
        id=f"request:{_hash(request)}",
        modality="json",
        content=request,
        schema="deepresearch.request@v1",
        facets={"domain": _DOMAIN, "ts": _now()},
        provenance={"parents": []},
        policy={"retention_days": 7},
    )
    region.write([atom])


def store_plan(plan: Dict[str, Any]) -> None:
    region = get_region(MEMORY_KEY)
    if region is None:
        return
    atom = Atom(
        id=f"plan:{_hash(plan)}",
        modality="json",
        content=plan,
        schema="deepresearch.plan@v1",
        facets={"domain": _DOMAIN, "ts": _now()},
        provenance={"parents": []},
        policy={"retention_days": 7},
    )
    region.write([atom])


def store_sources(sources: Iterable[Dict[str, Any]]) -> None:
    region = get_region(MEMORY_KEY)
    if region is None:
        return

    atoms: List[Atom] = []
    for src in sources:
        atoms.append(
            Atom(
                id=f"source:{_hash(src)}",
                modality="json",
                content=src,
                schema="deepresearch.source@v1",
                facets={
                    "domain": _DOMAIN,
                    "ts": _now(),
                    "url": src.get("url"),
                    "query": src.get("query"),
                },
                provenance={"parents": []},
                policy={"retention_days": 14},
            )
        )
    if atoms:
        region.write(atoms)


def store_notes(notes: Dict[str, Any]) -> None:
    region = get_region(MEMORY_KEY)
    if region is None:
        return
    atom = Atom(
        id=f"notes:{_hash(notes)}",
        modality="json",
        content=notes,
        schema="deepresearch.notes@v1",
        facets={"domain": _DOMAIN, "ts": _now()},
        provenance={"parents": []},
        policy={"retention_days": 14},
    )
    region.write([atom])


def store_report(report: str, citations: List[Dict[str, Any]]) -> None:
    region = get_region(MEMORY_KEY)
    if region is None:
        return
    atom = Atom(
        id=f"report:{_hash(report)}",
        modality="text",
        content=report,
        schema="deepresearch.report@v1",
        facets={"domain": _DOMAIN, "ts": _now()},
        provenance={"parents": []},
        policy={"retention_days": 30},
    )
    citation_atom = Atom(
        id=f"citations:{_hash(citations)}",
        modality="json",
        content=citations,
        schema="deepresearch.citations@v1",
        facets={"domain": _DOMAIN, "ts": _now()},
        provenance={"parents": []},
        policy={"retention_days": 30},
    )
    region.write([atom, citation_atom])
