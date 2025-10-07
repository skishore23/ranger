"""Base region implementations for the topology system.

Simple, generic regions that can be extended for any use case.
"""
import time
from typing import Dict, Any, Iterable, List, Optional, Tuple
from topology.types import Atom, RegionKind


class BaseMemoryRegion:
    """Base memory region - stores and retrieves atoms."""
    
    key: str
    kind: RegionKind = "memory"
    
    def __init__(self, key: str, domain: str = "default"):
        self.key = key
        self.domain = domain
        self._storage: Dict[str, Atom] = {}
    
    def read(self, query: Dict[str, Any]) -> Iterable[Atom]:
        """Read atoms matching query."""
        for atom in self._storage.values():
            if self._matches_query(atom, query):
                yield atom
    
    def write(self, atoms: Iterable[Atom]) -> None:
        """Write atoms to storage."""
        for atom in atoms:
            self._storage[atom.id] = atom
    
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        """Summarize atoms for goal context."""
        atom_list = list(atoms)
        
        if not atom_list:
            return self._create_empty_summary(goal)
        
        summary_content = {
            "total_atoms": len(atom_list),
            "domains": list(set(atom.facets.get("domain", "unknown") for atom in atom_list)),
            "modalities": list(set(atom.modality for atom in atom_list)),
            "goal": goal
        }
        
        return Atom(
            id=f"summary:{int(time.time() * 1000)}",
            modality="json",
            content=summary_content,
            schema="memory.summary@v1",
            facets={
                "domain": self.domain,
                "source": self.key,
                "ts": int(time.time() * 1000),
                "trust": 0.8
            },
            provenance={"parents": [atom.id for atom in atom_list]},
            policy={}
        )
    
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]:
        """Reconcile two atoms - prefer newer."""
        if left.facets.get("ts", 0) > right.facets.get("ts", 0):
            return True, left, "newer_wins"
        else:
            return True, right, "newer_wins"
    
    def _matches_query(self, atom: Atom, query: Dict[str, Any]) -> bool:
        """Check if atom matches query criteria."""
        # Simple matching - can be overridden
        if "domain" in query and atom.facets.get("domain") != query["domain"]:
            return False
        if "modality" in query and atom.modality != query["modality"]:
            return False
        return True
    
    def _create_empty_summary(self, goal: Dict[str, Any]) -> Atom:
        """Create empty summary."""
        return Atom(
            id=f"empty_summary:{int(time.time() * 1000)}",
            modality="json",
            content={"total_atoms": 0, "goal": goal},
            schema="memory.summary@v1",
            facets={"domain": self.domain, "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": []},
            policy={}
        )


class BaseGuardRegion:
    """Base guard region - validates atoms for policy violations."""
    
    key: str
    kind: RegionKind = "guard"
    
    def __init__(self, key: str, policy: Dict[str, Any] = None):
        self.key = key
        self.policy = policy or {}
    
    def validate(self, atoms: Iterable[Atom]) -> Dict[str, Any]:
        """Validate atoms - override for specific policies."""
        return {"ok": True, "findings": []}
    
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        """Summarize with policy context."""
        atom_list = list(atoms)
        
        return Atom(
            id=f"guard_summary:{int(time.time() * 1000)}",
            modality="json",
            content={
                "total_atoms": len(atom_list),
                "policy": self.policy,
                "goal": goal
            },
            schema="guard.summary@v1",
            facets={"domain": "guard", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": [atom.id for atom in atom_list]},
            policy={}
        )
    
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]:
        """Reconcile - prefer stricter policy."""
        return True, left, "first_wins"


class BaseModelRegion:
    """Base model region - for LLM/ML inference."""
    
    key: str
    kind: RegionKind = "model"
    
    def __init__(self, key: str, model_name: str = "generic"):
        self.key = key
        self.model_name = model_name
    
    def infer(self, prompt: Dict[str, Any], window: Iterable[Atom], budget: Dict[str, Any]) -> Iterable[Atom]:
        """Infer - override for specific models."""
        # Placeholder implementation
        yield Atom(
            id=f"model_output:{int(time.time() * 1000)}",
            modality="text",
            content="Model output placeholder",
            schema="model.output@v1",
            facets={"domain": "model", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": []},
            policy={}
        )
    
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        """Summarize using model compression."""
        atom_list = list(atoms)
        
        return Atom(
            id=f"model_summary:{int(time.time() * 1000)}",
            modality="text",
            content=f"Model summary of {len(atom_list)} atoms",
            schema="model.summary@v1",
            facets={"domain": "model", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": [atom.id for atom in atom_list]},
            policy={}
        )
    
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]:
        """Reconcile - prefer higher confidence."""
        left_trust = left.facets.get("trust", 0.5)
        right_trust = right.facets.get("trust", 0.5)
        
        if right_trust > left_trust:
            return True, right, "higher_confidence_wins"
        else:
            return True, left, "higher_confidence_wins"


class BaseToolRegion:
    """Base tool region - for external tool execution."""
    
    key: str
    kind: RegionKind = "tool"
    
    def __init__(self, key: str, tool_name: str = "generic"):
        self.key = key
        self.tool_name = tool_name
    
    def act(self, window: Iterable[Atom]) -> Tuple[Iterable[Atom], Iterable[Dict[str, Any]]]:
        """Execute tool actions - override for specific tools."""
        # Placeholder implementation
        result_atom = Atom(
            id=f"tool_result:{int(time.time() * 1000)}",
            modality="json",
            content={"action": "executed", "tool": self.tool_name},
            schema="tool.result@v1",
            facets={"domain": "tool", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": []},
            policy={}
        )
        
        effect = {"type": "tool_executed", "tool": self.tool_name}
        return [result_atom], [effect]
    
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        """Summarize tool execution."""
        atom_list = list(atoms)
        
        return Atom(
            id=f"tool_summary:{int(time.time() * 1000)}",
            modality="json",
            content={
                "total_atoms": len(atom_list),
                "tool": self.tool_name,
                "goal": goal
            },
            schema="tool.summary@v1",
            facets={"domain": "tool", "source": self.key, "ts": int(time.time() * 1000)},
            provenance={"parents": [atom.id for atom in atom_list]},
            policy={}
        )
    
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]:
        """Reconcile - prefer most recent execution."""
        left_ts = left.facets.get("ts", 0)
        right_ts = right.facets.get("ts", 0)
        
        if right_ts > left_ts:
            return True, right, "most_recent_wins"
        else:
            return True, left, "most_recent_wins"
