"""Core data models for the executable knowledge pack system.

All models are frozen dataclasses — immutable, hashable, serializable.
They form the typed contract between packs, recipe compiler, builder,
validator, and observation renderer.

Design authority: v14_next/EXPERT_KNOWLEDGE_ARCHITECTURE.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Pack descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackDescriptor:
    """Static metadata about a knowledge pack."""

    pack_id: str                          # e.g. "pdf", "sfnt", "elf"
    carrier_families: tuple[str, ...]     # e.g. ("pdf", "ps")
    supported_versions: tuple[str, ...]   # e.g. ("1.4", "1.5", "1.7", "2.0")
    capabilities: frozenset[str]          # {"detect","parse","build","validate","repair","transcript"}
    required_backends: tuple[str, ...]    # e.g. ("pikepdf", "construct")
    knowledge_revision: str               # e.g. "2026.07.1"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectionResult:
    """Evidence-based decision from pack.detect().

    Keywords in description text can only produce *candidate* decisions.
    Confirmed requires hard evidence: corpus magic, harness API, source-backed hints.
    """

    decision: Literal["confirmed", "candidate", "rejected", "insufficient"]
    score: float
    positive_evidence_ids: tuple[str, ...] = ()
    contradictory_evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldInfo:
    """A single structural field within a parsed artifact."""

    name: str                             # e.g. "pdf.xref.offset"
    offset: int                           # byte offset from start of artifact
    width: int                            # byte width (1/2/4/8)
    value: Any                            # current value (int/str/bytes)
    node_id: str                          # structural node identifier
    derived: bool = False                 # True if auto-recomputed on build
    protected: bool = False               # True if mutation must not overwrite


@dataclass(frozen=True)
class ParseResult:
    """Result of pack.parse(artifact_bytes)."""

    status: Literal["success", "partial", "failed", "backend_unavailable"]
    carrier_family: str = ""
    version: str = ""
    structural_summary: dict[str, Any] = field(default_factory=dict)
    field_map: dict[str, FieldInfo] = field(default_factory=dict)  # name -> FieldInfo
    node_count: int = 0
    parse_warnings: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Carrier contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CarrierContract:
    """What a pack guarantees about the carrier format."""

    format_id: str                        # e.g. "pdf-1.7"
    seed_required: bool = True
    minimal_seed_size: int = 0
    required_fields: tuple[str, ...] = ()
    derived_fields: tuple[str, ...] = ()  # auto-recomputed on build
    protected_fields: tuple[str, ...] = ()  # must not be overwritten by repair
    harness_acceptance_hints: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Recipe plan — typed replacement for the old dict-based recipe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecipeOperation:
    """A single typed operation within a recipe plan."""

    op_id: str
    kind: str                             # "set_field","mutate_field","recompute","insert","truncate","ast_transform"
    target_node_id: str | None = None
    read_spans: tuple[tuple[int, int], ...] = ()   # (start, end) byte ranges
    write_spans: tuple[tuple[int, int], ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    invalidated_derivations: tuple[str, ...] = ()  # derived fields to recompute
    rollback_hint: str = ""
    evidence_id: str = ""
    # For AST-level ops (structured_text pack), not byte offsets
    ast_transform: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Invariant:
    """A constraint that should hold after all operations are applied."""

    invariant_id: str
    kind: str                             # "checksum","length","offset","reference","state"
    expression: str = ""                  # human-readable expression
    protected: bool = False               # True = must not be broken by mutation


@dataclass(frozen=True)
class ExpectedEffect:
    """What the mutation is supposed to achieve at runtime."""

    effect_id: str
    target_expression: str = ""           # e.g. "pdf.object.7.stream.length"
    desired_relation: str = ""            # e.g. "decoded_length > allocation_size"
    expected_runtime_probe: str = ""      # e.g. "sink_reached_trigger_unmet -> crash"


@dataclass(frozen=True)
class RecipePlan:
    """Typed, versioned replacement for the old dict-based poc_recipe.

    Carries full provenance: every operation has evidence, every invariant
    is checkable, every expected effect is observable.
    """

    recipe_id: str = ""
    schema_version: str = "2.0"
    objective_id: str = ""
    carrier_contract_id: str = ""
    seed_id: str = ""
    operations: tuple[RecipeOperation, ...] = ()
    invariants: tuple[Invariant, ...] = ()
    expected_effects: tuple[ExpectedEffect, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    knowledge_revision: str = ""
    # Backward compat: the old dict carrier/transcript/rewrite keys
    carrier: dict[str, Any] = field(default_factory=dict)
    trigger_mutations: list[dict[str, Any]] = field(default_factory=list)
    open_gaps: list[str] = field(default_factory=list)
    sanity_expectations: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuildResult:
    """Result of pack.build(seed_bytes, plan)."""

    status: Literal["success", "partial", "failed", "backend_unavailable", "nonconvergent"]
    artifact_path: str = ""
    applied_operations: tuple[str, ...] = ()
    blocked_operations: tuple[str, ...] = ()
    mutation_intent_preserved: bool = True
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationFinding:
    """A single finding from one validator at one layer."""

    validator_id: str
    layer: Literal["byte_safety", "structural_parse", "invariant_check",
                   "harness_acceptance", "mutation_intent"]
    verdict: Literal["pass", "warn", "fail", "unknown"]
    strength: Literal["authoritative", "strong", "supporting", "heuristic"]
    invariant_id: str | None = None
    evidence_ref: str = ""
    repair_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    """Multi-layer validation result from pack.validate()."""

    candidate_path: str = ""
    pack_id: str = ""
    findings: tuple[ValidationFinding, ...] = ()
    overall_verdict: Literal["pass", "warn", "fail"] = "pass"
    blocks_submit: bool = False


# ---------------------------------------------------------------------------
# Pack mode — format-driven behavior switching
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackMode:
    """Active pack mode — determines how format-specific knowledge drives behavior.

    Stored on state.pack_mode (as dict for serialization), persists across turns.
    Upgraded by re-evaluation; never downgraded without explicit reason.
    """

    mode: Literal["unconfirmed", "candidate", "confirmed"] = "unconfirmed"
    pack_id: str = ""                    # e.g. "pdf", "sfnt"
    detection_score: float = 0.0
    positive_evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    confirmed_at_step: int = -1          # step when mode became confirmed/candidate
    upgrade_history: tuple[str, ...] = ()  # ["unconfirmed->candidate@step3", ...]


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairAction:
    """A typed repair action from pack.explain_repair()."""

    action_id: str
    kind: str                             # "recompute","fix_field","realign","restore"
    target_node_id: str | None = None
    description: str = ""
    evidence_ref: str = ""
