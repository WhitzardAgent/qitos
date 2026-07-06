"""Knowledge pack validation — bridge between five-layer pack validation and existing sanity.

When a KnowledgePack is confirmed for the candidate's format, this module
runs the pack's validate() method and merges findings into the existing
PoCSanityResult.  Pack findings with authoritative or strong strength
override generic findings; supporting and heuristic findings only add WARN.

Design authority: v14_next/EXPERT_KNOWLEDGE_ARCHITECTURE.md Section IV
"""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    CarrierContract,
    ExpectedEffect,
    ValidationFinding,
    ValidationReport,
)
from .registry import get_knowledge_registry
from .evidence import build_evidence_view

logger = logging.getLogger(__name__)


def validate_with_knowledge_pack(
    candidate_path: str,
    state: Any,
    mutation_intent: ExpectedEffect | None = None,
) -> ValidationReport | None:
    """Run five-layer validation through the matching knowledge pack.

    Returns None if no pack is confirmed for this format.
    """
    try:
        registry = get_knowledge_registry()
        if registry.is_empty():
            return None

        evidence = build_evidence_view(state)
        selected = registry.select_packs(evidence)

        if not selected:
            return None

        # Use the best-matching pack
        pack, det_result = selected[0]
        if det_result.decision not in ("confirmed", "candidate"):
            return None

        # Build carrier contract from state metadata
        carrier_contract = _build_carrier_contract(state, pack)

        # Read candidate bytes
        try:
            with open(candidate_path, "rb") as f:
                candidate_bytes = f.read()
        except OSError:
            return ValidationReport(
                candidate_path=candidate_path,
                pack_id=pack.descriptor.pack_id,
                findings=(ValidationFinding(
                    validator_id="knowledge_pack.io",
                    layer="byte_safety",
                    verdict="fail",
                    strength="authoritative",
                    evidence_ref="cannot_read_candidate",
                ),),
                overall_verdict="fail",
                blocks_submit=True,
            )

        # Run pack validation
        report = pack.validate(candidate_bytes, carrier_contract, mutation_intent)
        return report

    except Exception as e:
        logger.warning("Knowledge pack validation failed: %s", e)
        return None


def merge_pack_findings(
    sanity_result: Any,
    pack_report: ValidationReport | None,
) -> Any:
    """Merge pack validation findings into an existing PoCSanityResult.

    Rules:
    - authoritative findings: override generic findings with same category
    - strong findings: add as WARN or FAIL depending on verdict
    - supporting findings: add as WARN only (never FAIL)
    - heuristic findings: add as INFO only
    """
    if pack_report is None:
        return sanity_result

    from .sanity_bridge import validation_to_sanity_issues
    pack_issues = validation_to_sanity_issues(pack_report)

    # Add pack issues to sanity result
    for issue in pack_issues:
        # Check if there's already an existing issue with the same category
        existing = [
            i for i in sanity_result.issues
            if hasattr(i, "category") and i.category == issue.category
        ]

        if existing and issue.severity == "fail":
            # Pack found a more authoritative failure — upgrade
            for existing_issue in existing:
                if hasattr(existing_issue, "severity"):
                    existing_issue.severity = "fail"
        else:
            sanity_result.issues.append(issue)

    # Re-evaluate passed flag
    has_fail = any(
        i.severity == "fail"
        for i in sanity_result.issues
        if hasattr(i, "severity")
    )
    sanity_result.passed = not has_fail

    return sanity_result


def _build_carrier_contract(state: Any, pack: Any) -> CarrierContract:
    """Build a CarrierContract from state metadata."""
    metadata = getattr(state, "metadata", {}) or {}

    # Try to get existing carrier contract
    existing = metadata.get("carrier_contract")
    if isinstance(existing, CarrierContract):
        return existing
    if isinstance(existing, dict):
        try:
            return CarrierContract(**existing)
        except Exception:
            pass

    # Build a minimal contract from pack descriptor
    desc = pack.descriptor
    return CarrierContract(
        format_id=desc.carrier_families[0] if desc.carrier_families else "unknown",
        seed_required=True,
        minimal_seed_size=0,
        required_fields=(),
        derived_fields=(),
        protected_fields=(),
    )
