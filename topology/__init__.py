"""Public interface for the lightweight topology system."""

from .types import Atom, Budget, Region, Path, ContextWindow, Modality, RegionKind
from .registry import register_region, get_region, list_regions, clear_registry, has_region
from .planner import plan_path
from .stitch import summarize_regions, reconcile_overlaps, repair_context
from .packer import pack_context
from .lenses import args_only, redact, apply_modality_caps
from .attest import (
    create_atom_id,
    sign_atom,
    verify_atom,
    verify_atom_chain,
    create_provenance_chain,
    extract_audit_trail,
)

__all__ = [
    "Atom",
    "Budget",
    "Region",
    "Path",
    "ContextWindow",
    "Modality",
    "RegionKind",
    "register_region",
    "get_region",
    "list_regions",
    "clear_registry",
    "has_region",
    "plan_path",
    "summarize_regions",
    "reconcile_overlaps",
    "repair_context",
    "pack_context",
    "args_only",
    "redact",
    "apply_modality_caps",
    "create_atom_id",
    "sign_atom",
    "verify_atom",
    "verify_atom_chain",
    "create_provenance_chain",
    "extract_audit_trail",
]
