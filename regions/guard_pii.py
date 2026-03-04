"""PII guard region with detection, severity classification, and redaction."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, Iterable, List, Tuple

from topology.types import Atom
from .base import BaseGuardRegion


_PATTERNS: Dict[str, tuple[str, str]] = {
    "ssn": (r"\b\d{3}-\d{2}-\d{4}\b", "high"),
    "credit_card": (r"\b(?:\d{4}[- ]?){3}\d{4}\b", "high"),
    "email": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "medium"),
    "phone": (r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b", "medium"),
    "ip_address": (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "low"),
    "url": (r"\bhttps?://[^\s]+", "low"),
}

_REPLACEMENTS: Dict[str, str] = {
    "ssn": "[SSN_REDACTED]",
    "credit_card": "[CREDIT_CARD_REDACTED]",
    "email": "[EMAIL_REDACTED]",
    "phone": "[PHONE_REDACTED]",
    "ip_address": "[IP_REDACTED]",
    "url": "[URL_REDACTED]",
}


def _stable_id(prefix: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()[:16]}"


class GuardPII(BaseGuardRegion):
    """Guard that flags PII-like strings and can redact them."""

    def __init__(self, key: str, mode: str = "mask"):
        super().__init__(key=key, policy={"mode": mode})
        self.mode = mode
        self.severity = 2 if mode == "block" else 1

    def _detect(self, atom: Atom) -> List[Dict[str, Any]]:
        text = str(atom.content)
        findings: List[Dict[str, Any]] = []
        for pii_type, (pattern, severity) in _PATTERNS.items():
            if re.search(pattern, text):
                findings.append(
                    {
                        "atom_id": atom.id,
                        "pii_type": pii_type,
                        "severity": severity,
                        "match": True,
                    }
                )
        return findings

    def validate(self, atoms: Iterable[Atom]) -> Dict[str, Any]:
        findings_atoms: List[Atom] = []
        all_findings: List[Dict[str, Any]] = []

        for atom in atoms:
            detections = self._detect(atom)
            for detected in detections:
                all_findings.append(detected)
                finding_payload = {
                    "guard": self.key,
                    "atom_id": atom.id,
                    "pii_type": detected["pii_type"],
                    "severity": detected["severity"],
                }
                findings_atoms.append(
                    Atom(
                        id=_stable_id("finding", finding_payload),
                        modality="json",
                        content=detected,
                        schema="guard.finding.pii@v1",
                        facets={
                            "source": self.key,
                            "domain": "guard",
                            "ts": int(time.time() * 1000),
                            "trust": 0.9,
                        },
                        provenance={"parents": [atom.id]},
                        policy={"pii": True},
                    )
                )

        return {"ok": len(all_findings) == 0, "findings": findings_atoms}

    def redact_atoms(self, atoms: Iterable[Atom], findings: Iterable[Atom]) -> Iterable[Atom]:
        by_atom_id: Dict[str, List[Dict[str, Any]]] = {}
        for finding in findings:
            payload = finding.content if isinstance(finding.content, dict) else {}
            atom_id = payload.get("atom_id")
            if atom_id:
                by_atom_id.setdefault(atom_id, []).append(payload)

        redacted: List[Atom] = []
        for atom in atoms:
            matches = by_atom_id.get(atom.id, [])
            if not matches:
                redacted.append(atom)
                continue

            content = str(atom.content)
            pii_types = []
            for match in matches:
                pii_type = str(match.get("pii_type"))
                if pii_type not in _PATTERNS:
                    continue
                pii_types.append(pii_type)
                pattern = _PATTERNS[pii_type][0]
                replacement = _REPLACEMENTS[pii_type]
                content = re.sub(pattern, replacement, content)

            policy = dict(atom.policy or {})
            policy.update({"redacted": True, "pii_types": sorted(set(pii_types))})
            redacted.append(
                Atom(
                    id=atom.id,
                    modality=atom.modality,
                    content=content,
                    schema=atom.schema,
                    facets=dict(atom.facets or {}),
                    provenance=dict(atom.provenance or {}),
                    policy=policy,
                )
            )
        return redacted

    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        atom_list = list(atoms)
        redacted_atoms = list(self.redact_atoms(atom_list, self.validate(atom_list).get("findings", [])))
        pii_types = sorted(
            {
                t
                for atom in redacted_atoms
                for t in (atom.policy or {}).get("pii_types", [])
            }
        )
        count_redacted = sum(1 for atom in redacted_atoms if (atom.policy or {}).get("redacted"))
        summary_payload = {
            "guard": self.key,
            "goal": goal,
            "parents": [a.id for a in atom_list],
            "redacted_atoms": count_redacted,
            "pii_types": pii_types,
        }
        return Atom(
            id=_stable_id("guard_summary", summary_payload),
            modality="json",
            content={
                "goal": goal,
                "total_atoms": len(atom_list),
                "redacted_atoms": count_redacted,
                "pii_types_found": pii_types,
            },
            schema="guard.summary.pii@v1",
            facets={"domain": "guard", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": [a.id for a in atom_list]},
            policy={"mode": self.mode},
        )

    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Atom | None, str | None]:
        left_redacted = bool((left.policy or {}).get("redacted"))
        right_redacted = bool((right.policy or {}).get("redacted"))
        if left_redacted == right_redacted:
            newer = left if left.facets.get("ts", 0) >= right.facets.get("ts", 0) else right
            return True, newer, "newer_wins"
        return (True, left, "stricter_redaction_wins") if left_redacted else (True, right, "stricter_redaction_wins")
