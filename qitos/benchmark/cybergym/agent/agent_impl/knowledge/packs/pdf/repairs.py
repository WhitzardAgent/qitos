"""PDF repair actions — typed repair strategies from validation findings.

Each repair targets a specific validation failure and produces a
deterministic fix.  No LLM guessing — each action is programmatically
applicable.
"""

from __future__ import annotations

import logging
from typing import Any

from ...models import RepairAction, ValidationFinding, ValidationReport

logger = logging.getLogger(__name__)

# Map from (validator_id pattern) → repair function
_REPAIR_REGISTRY: dict[str, Any] = {}


def explain_pdf_repairs(report: ValidationReport) -> tuple[RepairAction, ...]:
    """Generate repair actions from a PDF validation report."""
    repairs: list[RepairAction] = []

    for finding in report.findings:
        if finding.verdict == "pass":
            continue

        for repair_action in _repairs_for_finding(finding):
            if repair_action not in repairs:
                repairs.append(repair_action)

    return tuple(repairs)


def _repairs_for_finding(finding: ValidationFinding) -> list[RepairAction]:
    """Map a finding to zero or more repair actions."""
    vid = finding.validator_id
    actions: list[RepairAction] = []

    if vid == "pdf.byte_safety.magic":
        actions.append(RepairAction(
            action_id="repair_fix_header_magic",
            kind="fix_field",
            target_node_id="header",
            description="Write %PDF-1.x magic at offset 0",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.structural.parse" and finding.verdict == "fail":
        actions.append(RepairAction(
            action_id="repair_recompute_xref",
            kind="recompute",
            target_node_id="xref",
            description="Rebuild xref table from object scan and update startxref",
            evidence_ref=finding.evidence_ref,
        ))
        actions.append(RepairAction(
            action_id="repair_fix_stream_length",
            kind="fix_field",
            target_node_id="stream",
            description="Recompute /Length for streams with inconsistent lengths",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.structural.root" and finding.verdict == "warn":
        actions.append(RepairAction(
            action_id="repair_restore_trailer_root",
            kind="restore",
            target_node_id="trailer",
            description="Restore /Root reference in trailer from catalog object",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.invariant.root" and finding.verdict == "fail":
        actions.append(RepairAction(
            action_id="repair_restore_trailer_root_inv",
            kind="restore",
            target_node_id="trailer",
            description="Restore /Root reference in trailer",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.mutation.roundtrip" and finding.verdict == "warn":
        actions.append(RepairAction(
            action_id="repair_bypass_pikepdf_save",
            kind="fix_field",
            target_node_id=None,
            description="Use raw byte mutation to bypass pikepdf auto-repair",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.mutation.raw_marker" and finding.verdict == "fail":
        actions.append(RepairAction(
            action_id="repair_reapply_pdf_raw_marker",
            kind="fix_field",
            target_node_id=None,
            description="Reapply the declared raw trigger after PDF carrier repair",
            evidence_ref=finding.evidence_ref,
        ))

    elif vid == "pdf.stream.length_mismatch" and finding.verdict == "warn":
        actions.append(RepairAction(
            action_id="repair_pdf_stream_length",
            kind="fix_field",
            target_node_id="stream",
            description="Recompute /Length for PDF streams unless the mismatch is the intended trigger",
            evidence_ref=finding.evidence_ref,
        ))

    return actions
