"""Executable knowledge pack system for CyberGym agent.

Transforms hand-written keyword rules into typed, evidence-based
knowledge packs backed by mature libraries.

Key types:
  - KnowledgePack: Protocol for detect→parse→build→validate pipeline
  - PackDescriptor: Static metadata about a pack
  - DetectionResult: Evidence-based detection decision
  - EvidenceView: Read-only task evidence snapshot (from state)
  - RecipePlan: Typed recipe with DAG operations and invariants
  - ValidationReport: Five-layer validation with strength arbitration

Usage:
  from agent_impl.knowledge import get_knowledge_registry, build_evidence_view
  registry = get_knowledge_registry()
  evidence = build_evidence_view(state)
  packs = registry.select_packs(evidence)
"""

from .backend_registry import BackendStatus, BackendRegistry, get_backend_registry
from .capability_matrix import TaskCapability, generate_capability_matrix, compute_coverage_summary
from .corpus import SeedRecord, SeedSelection, SeedSelector, build_seed_records, load_pack_seed_index, select_seed_for_pack
from .evidence import EvidenceView, build_evidence_view, eager_pack_select, maybe_upgrade_pack_mode, activate_pack_from_tool
from .models import (
    BuildResult,
    CarrierContract,
    DetectionResult,
    ExpectedEffect,
    FieldInfo,
    Invariant,
    PackDescriptor,
    PackMode,
    ParseResult,
    RecipeOperation,
    RecipePlan,
    RepairAction,
    ValidationFinding,
    ValidationReport,
)
from .protocol import KnowledgePack
from .recipe_ir import topological_sort_ops, detect_conflicts, apply_backpatch, recipe_from_dict, recipe_to_dict
from .registry import KnowledgeRegistry, get_knowledge_registry
from .unknown_resolver import UnknownDomainResolver, UnknownDomainResult
from .validation import validate_with_knowledge_pack, merge_pack_findings, validation_report_to_dict

__all__ = [
    # Registry
    "KnowledgeRegistry",
    "get_knowledge_registry",
    "BackendRegistry",
    "get_backend_registry",
    "BackendStatus",
    # Evidence
    "EvidenceView",
    "build_evidence_view",
    "eager_pack_select",
    "maybe_upgrade_pack_mode",
    "activate_pack_from_tool",
    # Protocol
    "KnowledgePack",
    # Models
    "PackDescriptor",
    "PackMode",
    "DetectionResult",
    "ParseResult",
    "FieldInfo",
    "CarrierContract",
    "RecipePlan",
    "RecipeOperation",
    "Invariant",
    "ExpectedEffect",
    "BuildResult",
    "ValidationFinding",
    "ValidationReport",
    "RepairAction",
    # Recipe IR
    "topological_sort_ops",
    "detect_conflicts",
    "apply_backpatch",
    "recipe_from_dict",
    "recipe_to_dict",
    # Corpus intelligence
    "SeedRecord",
    "SeedSelection",
    "SeedSelector",
    "build_seed_records",
    "load_pack_seed_index",
    "select_seed_for_pack",
    # Capability matrix
    "TaskCapability",
    "generate_capability_matrix",
    "compute_coverage_summary",
    # Unknown domain resolver
    "UnknownDomainResolver",
    "UnknownDomainResult",
    # Validation bridge
    "validate_with_knowledge_pack",
    "merge_pack_findings",
    "validation_report_to_dict",
]
