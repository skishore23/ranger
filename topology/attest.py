"""Attestation helpers used by topology regions and tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from typing import Dict, Iterable, List, Sequence

from .types import Atom


def _canonical_payload(content: object, schema: str | None, facets: Dict[str, object]) -> str:
    """Return JSON payload used for hashing/signing."""

    payload = {
        "content": content,
        "schema": schema,
        "facets": facets,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def create_atom_id(content: object, schema: str | None, facets: Dict[str, object]) -> str:
    """Create a deterministic SHA256-based atom identifier."""

    digest = hashlib.sha256(_canonical_payload(content, schema, facets).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def sign_atom(atom: Atom, secret_key: str, *, version: str = "v1") -> Atom:
    """Return a copy of ``atom`` with provenance signature metadata."""

    payload = _canonical_payload(atom.content, atom.schema, atom.facets)
    signature = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    signed_at = int(time.time() * 1000)

    provenance = dict(atom.provenance or {})
    provenance.update(
        {
            "sig": signature,
            "signed_at": signed_at,
            "signature_version": version,
        }
    )

    return replace(atom, provenance=provenance)


def verify_atom(atom: Atom, secret_key: str) -> bool:
    """Verify a signed atom with ``secret_key``."""

    if not atom.provenance or "sig" not in atom.provenance:
        return False

    expected = atom.provenance["sig"]
    payload = _canonical_payload(atom.content, atom.schema, atom.facets)
    actual = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(actual, expected)


def verify_atom_chain(atoms: Sequence[Atom], secret_key: str) -> Dict[str, object]:
    """Validate a chain of atoms ensuring all signatures and parents are sound."""

    invalid_atoms: List[Dict[str, object]] = []
    missing_signatures: List[str] = []
    seen: set[str] = set()
    chain_integrity = True

    for atom in atoms:
        if not atom.provenance or "sig" not in atom.provenance:
            missing_signatures.append(atom.id)
        elif not verify_atom(atom, secret_key):
            invalid_atoms.append({"atom_id": atom.id, "reason": "invalid_signature"})

        parents = list((atom.provenance or {}).get("parents", []))
        if any(parent not in seen for parent in parents):
            chain_integrity = False

        seen.add(atom.id)

    valid = not invalid_atoms and not missing_signatures and chain_integrity
    return {
        "valid": valid,
        "invalid_atoms": invalid_atoms,
        "missing_signatures": missing_signatures,
        "chain_integrity": chain_integrity,
    }


def create_provenance_chain(atoms: Sequence[Atom]) -> List[Atom]:
    """Inject sequential provenance metadata into ``atoms``."""

    total = len(atoms)
    chained: List[Atom] = []
    for index, atom in enumerate(atoms):
        parents = [item.id for item in chained]
        provenance = dict(atom.provenance or {})
        provenance.update(
            {
                "parents": parents,
                "chain_position": index,
                "chain_length": total,
            }
        )
        chained.append(replace(atom, provenance=provenance))
    return chained


def extract_audit_trail(atoms: Iterable[Atom]) -> List[Dict[str, object]]:
    """Produce audit-trail friendly dictionaries for ``atoms``."""

    trail: List[Dict[str, object]] = []
    for atom in atoms:
        facets = atom.facets or {}
        provenance = atom.provenance or {}
        trail.append(
            {
                "atom_id": atom.id,
                "modality": atom.modality,
                "schema": atom.schema,
                "domain": facets.get("domain"),
                "source": facets.get("source"),
                "trust": facets.get("trust"),
                "ts": facets.get("ts"),
                "parents": list(provenance.get("parents", [])),
                "cost": provenance.get("cost"),
                "policy": atom.policy,
            }
        )
    return trail


__all__ = [
    "create_atom_id",
    "sign_atom",
    "verify_atom",
    "verify_atom_chain",
    "create_provenance_chain",
    "extract_audit_trail",
]
