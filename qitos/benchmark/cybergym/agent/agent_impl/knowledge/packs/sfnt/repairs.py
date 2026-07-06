"""SFNT/Font repair actions — typed repair strategies from validation findings."""

from __future__ import annotations

import logging

from ...models import RepairAction, ValidationReport

logger = logging.getLogger(__name__)


def explain_sfnt_repairs(report: ValidationReport) -> tuple[RepairAction, ...]:
    """Generate repair actions from an SFNT validation report."""
    repairs: list[RepairAction] = []

    for finding in report.findings:
        if finding.verdict == "pass":
            continue

        vid = finding.validator_id

        if vid == "sfnt.structural.parse" and finding.verdict == "fail":
            repairs.append(RepairAction(
                action_id="repair_fix_table_directory",
                kind="realign",
                target_node_id="header",
                description="Fix table directory entries and realign tables to 4-byte boundaries",
                evidence_ref=finding.evidence_ref,
            ))
            repairs.append(RepairAction(
                action_id="repair_recompute_checksums",
                kind="recompute",
                target_node_id=None,
                description="Recompute all table checksums and head.checkSumAdjustment",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid == "sfnt.byte_safety.magic" and finding.verdict == "warn":
            repairs.append(RepairAction(
                action_id="repair_fix_sfnt_magic",
                kind="fix_field",
                target_node_id="header",
                description="Write valid SFNT magic bytes at offset 0",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid == "sfnt.mutation.roundtrip" and finding.verdict == "warn":
            repairs.append(RepairAction(
                action_id="repair_bypass_fonttools_save",
                kind="fix_field",
                target_node_id=None,
                description="Use raw byte mutation to bypass fontTools auto-normalization",
                evidence_ref=finding.evidence_ref,
            ))

    return tuple(repairs)
