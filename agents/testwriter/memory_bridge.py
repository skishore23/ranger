"""Helpers for interacting with registered memory regions from the test writer pipeline."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, Iterable, List

from topology.registry import get_region
from topology.types import Atom

MEMORY_KEY = "testwriter.memory"


def store_module_indexes(modules: Iterable[Dict[str, object]]) -> None:
    """Persist module analysis results into the topology memory region."""
    region = get_region(MEMORY_KEY)
    if region is None:
        return

    atoms: List[Atom] = []
    for module in modules:
        payload = dict(module)
        identifier = _hash_content(payload)
        module_path = module.get("file_path") or module.get("path")
        atoms.append(
            Atom(
                id=f"module:{identifier}",
                modality="json",
                content=payload,
                schema="testwriter.module@v1",
                facets={
                    "domain": "testwriter",
                    "source": "analysis",
                    "path": module_path,
                    "ts": int(time.time() * 1000),
                },
                provenance={"parents": [], "cost": {}},
                policy={"retention_days": 3},
            )
        )

    if atoms:
        region.write(atoms)


def store_generated_tests(tests: Iterable[Dict[str, str]]) -> None:
    """Persist generated tests into the topology memory region."""
    region = get_region(MEMORY_KEY)
    if region is None:
        return

    atoms: List[Atom] = []
    for test in tests:
        identifier = _hash_content(test)
        atoms.append(
            Atom(
                id=f"test:{identifier}",
                modality="code",
                content=test.get("content", ""),
                schema="testwriter.test@v1",
                facets={
                    "domain": "testwriter",
                    "source": "generation",
                    "path": test.get("path"),
                    "ts": int(time.time() * 1000),
                },
                provenance={"parents": [], "cost": {}},
                policy={"retention_days": 7},
            )
        )

    if atoms:
        region.write(atoms)


def store_execution_result(result: Dict[str, object]) -> None:
    """Persist execution results for guard/context usage."""
    region = get_region(MEMORY_KEY)
    if region is None:
        return

    identifier = _hash_content(result)
    atom = Atom(
        id=f"execution:{identifier}",
        modality="json",
        content=result,
        schema="testwriter.execution@v1",
        facets={
            "domain": "testwriter",
            "source": "execution",
            "status": result.get("status"),
            "ts": int(time.time() * 1000),
        },
        provenance={"parents": [], "cost": {}},
        policy={"retention_days": 5},
    )
    region.write([atom])


def _hash_content(payload: Dict[str, object]) -> str:
    data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
