"""Packet repair actions — typed repair strategies for packet validation findings."""

from __future__ import annotations

import logging

from ...models import RepairAction, ValidationReport

logger = logging.getLogger(__name__)


def explain_packet_repairs(report: ValidationReport) -> tuple[RepairAction, ...]:
    """Generate repair actions from a packet validation report."""
    repairs: list[RepairAction] = []

    for finding in report.findings:
        if finding.verdict == "pass":
            continue

        vid = finding.validator_id

        if vid == "packet.structural.parse" and finding.verdict == "warn":
            repairs.append(RepairAction(
                action_id="repair_fix_checksum",
                kind="recompute",
                target_node_id=None,
                description="Recompute IP/TCP/UDP checksums after mutation",
                evidence_ref=finding.evidence_ref,
            ))
            repairs.append(RepairAction(
                action_id="repair_fix_selector",
                kind="fix_field",
                target_node_id=None,
                description="Fix protocol selector fields (UDP/TCP port, IP protocol)",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid == "packet.mutation.raw_marker" and finding.verdict == "fail":
            repairs.append(RepairAction(
                action_id="repair_reapply_packet_payload_marker",
                kind="fix_field",
                target_node_id=None,
                description="Reapply the declared payload trigger after checksum or selector repair",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid in {"packet.pcap.header", "packet.pcap.magic"} and finding.verdict in {"fail", "warn"}:
            repairs.append(RepairAction(
                action_id="repair_wrap_raw_frame_as_pcap",
                kind="restore",
                target_node_id="pcap.header",
                description="Wrap the raw frame with a pcap global header and packet record",
                evidence_ref=finding.evidence_ref,
            ))

        elif vid in {"packet.pcap.record", "packet.pcap.record_length"} and finding.verdict in {"fail", "warn"}:
            repairs.append(RepairAction(
                action_id="repair_pcap_record_length",
                kind="fix_field",
                target_node_id="pcap.record",
                description="Repair pcap packet record captured/original length fields",
                evidence_ref=finding.evidence_ref,
            ))

    return tuple(repairs)
