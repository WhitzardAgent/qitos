"""KnowledgePack protocol — the contract every domain pack must implement.

Each pack provides a full pipeline:
  detect → parse → derive_contract → plan → build → validate → explain_repair

The LLM's job is to select objectives, interpret evidence, and decide next
experiments.  The pack's job is to handle format-specific structure, construction,
and validation — not the LLM's job to remember PDF xref offsets or SFNT checksums.

Design authority: v14_next/EXPERT_KNOWLEDGE_ARCHITECTURE.md Section III
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import (
    BuildResult,
    CarrierContract,
    DetectionResult,
    ExpectedEffect,
    PackDescriptor,
    ParseResult,
    RecipePlan,
    RepairAction,
    ValidationReport,
)


@runtime_checkable
class KnowledgePack(Protocol):
    """Executable knowledge pack protocol.

    Packs are plugins: they declare capabilities via `descriptor`, and every
    method has a typed return.  Optional backend unavailable → return typed
    status (e.g. ParseResult(status="backend_unavailable")), never crash.
    """

    descriptor: PackDescriptor

    def detect(self, evidence: Any) -> DetectionResult:
        """Determine whether this pack applies to the current task.

        Evidence-based detection, not keyword guessing:
        - project_name in known set → candidate (weak)
        - corpus magic bytes match → confirmed (strong)
        - harness API matches → confirmed (strong)
        - source-backed format hints → confirmed (authoritative)

        Keywords alone can only produce *candidate* decisions.
        """
        ...

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        """Parse a binary artifact into structural fields.

        Returns a field_map of named structural nodes with offsets, widths,
        and values.  The field_map is the foundation for plan() and validate().
        """
        ...

    def derive_contract(
        self,
        parsed: ParseResult,
        harness: dict[str, Any] | None = None,
    ) -> CarrierContract:
        """Derive what the carrier format requires from the parsed structure
        and the harness contract.

        Produces a CarrierContract listing required, derived, and protected
        fields — the invariants that must hold for a valid candidate.
        """
        ...

    def plan(
        self,
        objective: dict[str, Any],
        provenance: dict[str, Any] | None = None,
        carrier: CarrierContract | None = None,
    ) -> RecipePlan:
        """Generate a typed recipe plan from objective + provenance + carrier.

        Operations include read/write spans, pre/postconditions, and
        invalidated derivations.  The plan is a DAG, not a flat list.
        """
        ...

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        """Build a candidate from seed bytes + recipe plan.

        Applies operations in dependency order, with fixed-point backpatch
        for derived fields (length, checksum, offset).  If the backpatch
        doesn't converge, returns status="nonconvergent".
        """
        ...

    def validate(
        self,
        artifact: bytes,
        contract: CarrierContract,
        mutation_intent: ExpectedEffect | None = None,
    ) -> ValidationReport:
        """Five-layer validation of a candidate.

        Layers: byte_safety → structural_parse → invariant_check →
                harness_acceptance → mutation_intent

        Critical: third-party libraries (pikepdf, fontTools) may auto-repair.
        Validator must compare raw bytes before/after round-trip to ensure
        the target malformed field is preserved.
        """
        ...

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        """Generate typed repair actions from a validation report.

        Each repair action targets a specific finding and can be applied
        programmatically (not by LLM guessing).
        """
        ...
