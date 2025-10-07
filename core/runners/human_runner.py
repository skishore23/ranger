"""Human collaboration runner implementation."""

from __future__ import annotations

import inspect
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from ..workspace import Snapshot
from ..capability import Capability


class HumanRunner:
    """Runner for human collaboration capabilities."""

    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        callback: Optional[Callable[..., Dict[str, Any]]] = None,
        write_keys: Optional[List[str]] = None,
    ):
        """Initialize human runner.

        Args:
            title: Title for the human review card.
            description: Description of what human should do.
            fields: Form fields for human input.
            callback: Optional function that maps responses/snapshot to workspace writes.
            write_keys: Declared output keys for the capability.
        """

        self.title = title
        self.description = description
        self.fields = fields or []
        self.callback = callback
        self.write_keys = write_keys or []

    def run(
        self,
        cap: Capability,
        snap: Snapshot,
        context: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Execute human capability with interactive CLI prompts when possible."""

        print(f"👤 Human input requested: {self.title}")
        print(f"Human review requested: {self.title}")
        if self.description:
            print(f"Description: {self.description}")
            print(f"   {self.description}")

        if self.write_keys and all(snap.exists(key) for key in self.write_keys):
            print("   Existing values detected; skipping human input.")
            return {}

        # Collect responses when running interactively. Fall back to existing values otherwise.
        responses: Dict[str, Any] = {}

        if self.fields and sys.stdin.isatty():
            for field in self.fields:
                name = field.get("name")
                if not name:
                    continue
                field_type = field.get("type", "text")
                label = field.get("label", name)
                env_key = os.getenv(f"RANGER_FIELD_{name.upper()}")

                if env_key is not None:
                    responses[name] = env_key
                    continue

                prompt = f"{label}: "
                if field_type == "select":
                    options = field.get("options", [])
                    prompt = f"{label} {options}: "
                    value = input(prompt).strip()
                    if not value and options:
                        value = str(options[0])
                elif field_type == "textarea":
                    print(f"{label} (finish with empty line):")
                    lines: List[str] = []
                    while True:
                        line = input().rstrip("\n")
                        if not line:
                            break
                        lines.append(line)
                    value = "\n".join(lines)
                else:
                    value = input(prompt).strip()
                responses[name] = value
        else:
            print("   (non-interactive mode detected; no input captured)")

        if self.callback:
            return self._invoke_callback(responses, snap)

        if len(self.write_keys) == 1 and responses:
            key = self.write_keys[0]
            value = responses.get(next(iter(responses)))
            if value is not None:
                return {key: value}

        return responses

    def _invoke_callback(self, responses: Dict[str, Any], snap: Snapshot) -> Dict[str, Any]:
        try:
            sig = inspect.signature(self.callback)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return self.callback(responses, snap)  # type: ignore[call-arg]

        params = sig.parameters
        if len(params) == 2:
            return self.callback(responses, snap)  # type: ignore[call-arg]
        if len(params) == 1:
            param = next(iter(params.values()))
            if param.annotation in (Snapshot, "Snapshot"):
                return self.callback(snap)  # type: ignore[call-arg]
            return self.callback(responses)  # type: ignore[call-arg]
        return self.callback()  # type: ignore[call-arg]
