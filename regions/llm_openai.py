"""Model region backed by the core OpenAI provider."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Iterable, List, Tuple, Optional

from topology.types import Atom, RegionKind, Budget
from core.errors import GoalBlocked
from core.llm.provider import OpenAIProvider


class LLMOpenAI:
    """Topology model region that proxies calls to OpenAI."""

    kind: RegionKind = "model"

    def __init__(
        self,
        key: str,
        model: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        system_prompt: str | None = None,
    ) -> None:
        self.key = key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or "You are a helpful assistant."
        self._provider = OpenAIProvider()
        self.trust = 0.6
        self.cost_profile = {
            "latency": 45.0,
            "tokens": float(self.max_tokens),
            "risk": 0.2,
            "trust": self.trust,
        }

    # Memory interface (unused)
    def read(self, query: Dict[str, Any]) -> Iterable[Atom]:  # pragma: no cover - not used
        return []

    def write(self, atoms: Iterable[Atom]) -> None:  # pragma: no cover - not used
        return None

    # Guard interface (unused)
    def validate(self, atoms: Iterable[Atom]) -> Dict[str, Any]:  # pragma: no cover - not used
        return {"ok": True, "findings": []}

    # Model interface
    def infer(
        self,
        prompt: Dict[str, Any],
        window: Iterable[Atom],
        budget: Dict[str, Any] | Budget | None = None,
    ) -> Iterable[Atom]:
        window_list = list(window)
        options = dict(prompt.get("options")) if isinstance(prompt.get("options"), dict) else {}

        system_prompt = options.get("system_prompt") or self.system_prompt
        model = options.get("model") or self.model

        budget_overrides: Dict[str, Any] = {}
        if isinstance(budget, Budget):
            budget_overrides["max_tokens"] = budget.tokens if budget.tokens else None
        elif isinstance(budget, dict):
            budget_overrides.update(budget)

        max_tokens = (
            options.get("max_tokens")
            or budget_overrides.get("max_tokens")
            or self.max_tokens
        )

        temperature_override = options.get("temperature")
        if temperature_override is None:
            temperature_override = budget_overrides.get("temperature")
        temperature = temperature_override if temperature_override is not None else self.temperature

        message = self._build_prompt(prompt, window_list)
        try:
            response = self._provider.generate(
                system=system_prompt,
                prompt=message,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                schema=prompt.get("schema"),
            )
        except Exception as exc:  # noqa: BLE001 - surface as goal block for retry
            raise GoalBlocked("llm_unavailable", details={"error": str(exc)}) from exc
        atom = self._create_atom(
            response,
            window_list,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield atom

    # Tool interface (unused)
    def act(self, window: Iterable[Atom]):  # pragma: no cover - not used
        return [], []

    # Common interface
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        atoms = list(atoms)
        return Atom(
            id=f"model_summary:{int(time.time() * 1000)}",
            modality="json",
            content={
                "total_atoms": len(atoms),
                "model": self.model,
                "goal": goal,
            },
            schema="model.summary@v1",
            facets={
                "domain": "model",
                "source": self.key,
                "ts": int(time.time() * 1000),
            },
            provenance={"parents": [atom.id for atom in atoms]},
            policy={"temperature": self.temperature},
        )

    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Atom, str]:
        left_trust = left.facets.get("trust", 0.5)
        right_trust = right.facets.get("trust", 0.5)
        if right_trust > left_trust:
            return True, right, "higher_trust"
        return True, left, "higher_trust"

    # Helpers
    def _build_prompt(self, prompt: Dict[str, Any], window: Iterable[Atom]) -> str:
        context_blocks: List[str] = []
        for atom in window:
            context_blocks.append(self._format_atom(atom))
        context_section = "\n\n".join(context_blocks) if context_blocks else "(no context)"
        base_prompt = prompt.get("text") or json.dumps(prompt, default=str)
        return f"{base_prompt}\n\nCONTEXT:\n{context_section}"

    def _format_atom(self, atom: Atom) -> str:
        snippet = atom.content
        if isinstance(snippet, dict):
            snippet = json.dumps(snippet, indent=2, default=str)
        elif not isinstance(snippet, str):
            snippet = str(snippet)
        return f"[{atom.schema or atom.modality}] {snippet[:500]}"

    def _create_atom(
        self,
        response: str,
        window: Iterable[Atom],
        *,
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Atom:
        content_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
        window_ids = [atom.id for atom in window]
        return Atom(
            id=f"model_output:{content_hash}",
            modality="text",
            content=response,
            schema="model.output@v1",
            facets={
                "domain": "model",
                "source": self.key,
                "model": model,
                "ts": int(time.time() * 1000),
                "trust": 0.6,
            },
            provenance={
                "parents": window_ids,
                "cost": {
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            },
            policy={"usage": "llm"},
        )
