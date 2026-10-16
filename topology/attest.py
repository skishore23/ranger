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
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def create_atom_id(content: object, schema: str | None, facets: Dict[str, object]) -> str:
    """Create a deterministic SHA256-based atom identifier."""

    digest = hashlib.sha256(_canonical_payload(content, schema, facets).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _signed_payload(atom: Atom) -> str:
    from core.serialization import dumps
    return dumps({"id": atom.id, "modality": atom.modality, "content": atom.content,
                  "schema": atom.schema, "facets": atom.facets, "policy": atom.policy,
                  "provenance": {k: v for k, v in atom.provenance.items() if k != "sig"}})


def sign_atom(atom: Atom, secret_key: str, *, version: str = "v2") -> Atom:
    """Authenticate all fields and provenance except the signature itself.

    Legacy v1 signatures are intentionally not generated or accepted.
    """
    if version != "v2":
        raise ValueError("Only v2 atom signatures are supported")
    provenance = {**(atom.provenance or {}), "signed_at": int(time.time() * 1000),
                  "signature_version": version}
    signed = replace(atom, provenance=provenance)
    provenance["sig"] = hmac.new(secret_key.encode(), _signed_payload(signed).encode(), hashlib.sha256).hexdigest()
    return signed


def verify_atom(atom: Atom, secret_key: str) -> bool:
    if not atom.provenance or atom.provenance.get("signature_version") != "v2":
        return False
    expected = atom.provenance.get("sig")
    if not isinstance(expected, str):
        return False
    try:
        actual = hmac.new(secret_key.encode(), _signed_payload(atom).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def verify_atom_chain(atoms: Sequence[Atom], secret_key: str) -> Dict[str, object]:
    """Validate a chain of atoms ensuring all signatures and parents are sound."""

    invalid_atoms: List[Dict[str, object]] = []
    missing_signatures: List[str] = []
    seen: set[str] = set()
    chain_integrity = True

    has_chain = any("chain_position" in (a.provenance or {}) or "chain_length" in (a.provenance or {}) for a in atoms)
    for index, atom in enumerate(atoms):
        if not atom.provenance or "sig" not in atom.provenance:
            missing_signatures.append(atom.id)
        elif not verify_atom(atom, secret_key):
            invalid_atoms.append({"atom_id": atom.id, "reason": "invalid_signature"})

        provenance = atom.provenance or {}
        parents = provenance.get("parents", [])
        if not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents):
            chain_integrity = False
            parents = []
        if atom.id in seen:
            chain_integrity = False
        if has_chain and (type(provenance.get("chain_position")) is not int
                          or type(provenance.get("chain_length")) is not int
                          or provenance.get("chain_position") != index
                          or provenance.get("chain_length") != len(atoms)
                          or parents != [a.id for a in atoms[:index]]):
            chain_integrity = False
        if any(parent not in seen for parent in parents):
            chain_integrity = False

        seen.add(atom.id)

    valid = bool(atoms) and not invalid_atoms and not missing_signatures and chain_integrity
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
