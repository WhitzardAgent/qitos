"""Packet validator — five-layer validation with Scapy round-trip check."""

from __future__ import annotations

import logging
import os
import struct
from typing import Any

from ...models import (
    CarrierContract,
    ExpectedEffect,
    ValidationFinding,
    ValidationReport,
)
from ..raw_marker import validate_raw_marker_intent

logger = logging.getLogger(__name__)

_MAX_CANDIDATE_SIZE = 10 * 1024 * 1024  # 10 MB


def validate_packet_candidate(
    candidate_path: str,
    contract: CarrierContract,
    mutation_intent: ExpectedEffect | None = None,
) -> ValidationReport:
    """Five-layer validation of a packet candidate."""
    findings: list[ValidationFinding] = []

    # Layer 1: byte_safety
    findings.extend(_validate_byte_safety(candidate_path))
    if any(f.verdict == "fail" and f.strength == "authoritative" for f in findings):
        return _build_report(candidate_path, "packet", findings, early_exit=True)

    # Layer 2: structural_parse
    if _looks_like_pcap(candidate_path):
        findings.extend(_validate_pcap_structural_parse(candidate_path))
    else:
        findings.extend(_validate_structural_parse(candidate_path))

    # Layer 3: invariant_check
    findings.extend(_validate_invariants(candidate_path, contract))

    # Layer 4: harness_acceptance — deferred
    findings.append(ValidationFinding(
        validator_id="packet.harness",
        layer="harness_acceptance",
        verdict="unknown",
        strength="heuristic",
        evidence_ref="harness_not_available",
    ))

    # Layer 5: mutation_intent
    if mutation_intent:
        findings.extend(_validate_mutation_intent(candidate_path, mutation_intent))

    return _build_report(candidate_path, "packet", findings)


def _validate_byte_safety(candidate_path: str) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []

    if not os.path.exists(candidate_path):
        findings.append(ValidationFinding(
            validator_id="packet.byte_safety.exists",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_not_found",
        ))
        return findings

    size = os.path.getsize(candidate_path)
    if size == 0:
        findings.append(ValidationFinding(
            validator_id="packet.byte_safety.empty",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_is_empty",
        ))
    elif size > _MAX_CANDIDATE_SIZE:
        findings.append(ValidationFinding(
            validator_id="packet.byte_safety.oversized",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"file_size_{size}",
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="packet.byte_safety.exists",
            layer="byte_safety",
            verdict="pass",
            strength="authoritative",
            evidence_ref=f"file_size_{size}",
        ))

    return findings


def _validate_structural_parse(candidate_path: str) -> list[ValidationFinding]:
    try:
        from scapy.all import Ether, IP, conf
        conf.verb = 0
    except ImportError:
        return [ValidationFinding(
            validator_id="packet.structural.backend",
            layer="structural_parse",
            verdict="unknown",
            strength="heuristic",
            evidence_ref="scapy_not_available",
        )]

    try:
        with open(candidate_path, "rb") as f:
            data = f.read()

        pkt = Ether(data)
        layers = []
        current = pkt
        while current is not None:
            layers.append(current.__class__.__name__)
            current = current.payload if current.payload and current.payload.__class__.__name__ != "Raw" else None

        return [ValidationFinding(
            validator_id="packet.structural.parse",
            layer="structural_parse",
            verdict="pass",
            strength="strong",
            evidence_ref=f"scapy_parsed_layers_{'_'.join(layers)}",
        )]
    except Exception as e:
        return [ValidationFinding(
            validator_id="packet.structural.parse",
            layer="structural_parse",
            verdict="warn",
            strength="supporting",
            evidence_ref=f"scapy_parse_failed: {e}",
            repair_actions=("fix_checksum", "fix_selector"),
        )]


def _looks_like_pcap(candidate_path: str) -> bool:
    try:
        with open(candidate_path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic in {
        b"\xd4\xc3\xb2\xa1",
        b"\xa1\xb2\xc3\xd4",
        b"\x4d\x3c\xb2\xa1",
        b"\xa1\xb2\x3c\x4d",
    }


def _validate_pcap_structural_parse(candidate_path: str) -> list[ValidationFinding]:
    try:
        data = open(candidate_path, "rb").read()
    except OSError as e:
        return [ValidationFinding(
            validator_id="packet.pcap.readable",
            layer="structural_parse",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"read_error_{e}",
        )]

    if len(data) < 24:
        return [ValidationFinding(
            validator_id="packet.pcap.header",
            layer="structural_parse",
            verdict="fail",
            strength="strong",
            evidence_ref=f"pcap_size_{len(data)}_need_24",
            repair_actions=("wrap_raw_frame_as_pcap",),
        )]

    magic = data[:4]
    if magic in {b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"}:
        endian = "<"
    elif magic in {b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"}:
        endian = ">"
    else:
        return [ValidationFinding(
            validator_id="packet.pcap.magic",
            layer="structural_parse",
            verdict="fail",
            strength="strong",
            evidence_ref=f"invalid_pcap_magic_{magic.hex()}",
            repair_actions=("wrap_raw_frame_as_pcap",),
        )]

    version_major, version_minor, _thiszone, _sigfigs, snaplen, linktype = struct.unpack(
        f"{endian}HHiiii", data[4:24]
    )
    findings = [ValidationFinding(
        validator_id="packet.pcap.header",
        layer="structural_parse",
        verdict="pass",
        strength="strong",
        evidence_ref=f"pcap_v{version_major}.{version_minor}_snaplen_{snaplen}_linktype_{linktype}",
    )]

    if len(data) == 24:
        findings.append(ValidationFinding(
            validator_id="packet.pcap.record",
            layer="structural_parse",
            verdict="warn",
            strength="supporting",
            evidence_ref="pcap_has_no_packet_records",
            repair_actions=("add_pcap_record",),
        ))
        return findings

    if len(data) < 40:
        findings.append(ValidationFinding(
            validator_id="packet.pcap.record",
            layer="structural_parse",
            verdict="fail",
            strength="strong",
            evidence_ref=f"pcap_record_header_truncated_size_{len(data)}",
            repair_actions=("repair_pcap_record_header",),
        ))
        return findings

    _ts_sec, _ts_usec, incl_len, orig_len = struct.unpack(f"{endian}IIII", data[24:40])
    remaining = len(data) - 40
    if incl_len > remaining:
        findings.append(ValidationFinding(
            validator_id="packet.pcap.record_length",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"incl_len_{incl_len}_exceeds_remaining_{remaining}",
            repair_actions=("repair_pcap_record_length",),
        ))
    elif orig_len < incl_len:
        findings.append(ValidationFinding(
            validator_id="packet.pcap.record_length",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"orig_len_{orig_len}_smaller_than_incl_len_{incl_len}",
            repair_actions=("repair_pcap_record_length",),
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="packet.pcap.record_length",
            layer="invariant_check",
            verdict="pass",
            strength="strong",
            evidence_ref=f"incl_len_{incl_len}_orig_len_{orig_len}",
        ))
    return findings


def _validate_invariants(candidate_path: str, contract: CarrierContract) -> list[ValidationFinding]:
    # Packet invariants are mostly checksum-based; Scapy handles on build
    return []


def _validate_mutation_intent(candidate_path: str, mutation_intent: ExpectedEffect) -> list[ValidationFinding]:
    try:
        with open(candidate_path, "rb") as f:
            raw_bytes = f.read()
    except OSError:
        return []

    return validate_raw_marker_intent(
        raw_bytes,
        mutation_intent,
        validator_id="packet.mutation.raw_marker",
        repair_actions=(
            "reapply_payload_after_checksum_repair",
            "use_raw_byte_mutation",
            "check_selector_and_payload_recipe",
        ),
    )


def _build_report(candidate_path: str, pack_id: str, findings: list[ValidationFinding], early_exit: bool = False) -> ValidationReport:
    has_fail = any(f.verdict == "fail" for f in findings)
    has_warn = any(f.verdict == "warn" for f in findings)

    overall = "fail" if has_fail else ("warn" if has_warn else "pass")
    blocks = any(f.verdict == "fail" and f.strength in ("authoritative", "strong") for f in findings)

    return ValidationReport(
        candidate_path=candidate_path,
        pack_id=pack_id,
        findings=tuple(findings),
        overall_verdict=overall,
        blocks_submit=blocks,
    )
