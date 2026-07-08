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

        elif vid in {"sfnt.directory.header", "sfnt.directory.magic"} and finding.verdict == "fail":
            repairs.append(RepairAction(
                action_id="repair_sfnt_header",
                kind="fix_field",
                target_node_id="header",
                description="Restore SFNT magic and minimum offset-table header fields",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid == "sfnt.directory.search_params" and finding.verdict == "warn":
            repairs.append(RepairAction(
                action_id="repair_sfnt_search_params",
                kind="recompute",
                target_node_id="header",
                description="Recompute searchRange, entrySelector, and rangeShift from numTables",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid in {"sfnt.directory.range", "sfnt.directory.table_range"} and finding.verdict == "fail":
            repairs.append(RepairAction(
                action_id="repair_sfnt_table_directory_ranges",
                kind="realign",
                target_node_id="table_directory",
                description="Repair table directory offsets and lengths so every table range is inside the file",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid == "sfnt.directory.checksum" and finding.verdict == "warn":
            repairs.append(RepairAction(
                action_id="repair_sfnt_table_checksums",
                kind="recompute",
                target_node_id="table_directory",
                description="Recompute table checksums and head.checkSumAdjustment after mutation",
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

        elif vid == "sfnt.mutation.raw_marker" and finding.verdict == "fail":
            repairs.append(RepairAction(
                action_id="repair_reapply_sfnt_raw_marker",
                kind="fix_field",
                target_node_id=None,
                description="Reapply the declared raw trigger after SFNT table/checksum repair",
                evidence_ref=finding.evidence_ref,
            ))

    return tuple(repairs)
