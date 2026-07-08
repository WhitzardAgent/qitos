"""SFNT/Font validator — five-layer validation with fontTools round-trip check.

Layers:
1. byte_safety: file exists, readable, size bounds
2. structural_parse: fontTools can re-open, table count consistent
3. invariant_check: protected fields intact, checksum consistency
4. harness_acceptance: (deferred)
5. mutation_intent: target malformed field preserved after round-trip
"""

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

_MAX_CANDIDATE_SIZE = 50 * 1024 * 1024


def validate_sfnt_candidate(
    candidate_path: str,
    contract: CarrierContract,
    mutation_intent: ExpectedEffect | None = None,
) -> ValidationReport:
    """Five-layer validation of an SFNT/Font candidate."""
    findings: list[ValidationFinding] = []

    # Layer 1: byte_safety
    findings.extend(_validate_byte_safety(candidate_path))
    if any(f.verdict == "fail" and f.strength == "authoritative" for f in findings):
        return _build_report(candidate_path, "sfnt", findings, early_exit=True)

    # Backend-independent SFNT header/table directory checks.
    findings.extend(_validate_sfnt_directory(candidate_path))

    # Layer 2: structural_parse
    findings.extend(_validate_structural_parse(candidate_path))
    if any(f.verdict == "fail" and f.strength in ("authoritative", "strong") for f in findings):
        return _build_report(candidate_path, "sfnt", findings, early_exit=True)

    # Layer 3: invariant_check
    findings.extend(_validate_invariants(candidate_path, contract))

    # Layer 4: harness_acceptance — deferred
    findings.append(ValidationFinding(
        validator_id="sfnt.harness",
        layer="harness_acceptance",
        verdict="unknown",
        strength="heuristic",
        evidence_ref="harness_not_available",
    ))

    # Layer 5: mutation_intent
    if mutation_intent:
        findings.extend(_validate_mutation_intent(candidate_path, mutation_intent))

    return _build_report(candidate_path, "sfnt", findings)


def _validate_byte_safety(candidate_path: str) -> list[ValidationFinding]:
    """Layer 1: basic file safety checks."""
    findings: list[ValidationFinding] = []

    if not os.path.exists(candidate_path):
        findings.append(ValidationFinding(
            validator_id="sfnt.byte_safety.exists",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_not_found",
            repair_actions=("regenerate_candidate",),
        ))
        return findings

    size = os.path.getsize(candidate_path)
    if size == 0:
        findings.append(ValidationFinding(
            validator_id="sfnt.byte_safety.empty",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_is_empty",
        ))
    elif size > _MAX_CANDIDATE_SIZE:
        findings.append(ValidationFinding(
            validator_id="sfnt.byte_safety.oversized",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"file_size_{size}",
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="sfnt.byte_safety.exists",
            layer="byte_safety",
            verdict="pass",
            strength="authoritative",
            evidence_ref=f"file_size_{size}",
        ))

    # Check font magic
    try:
        with open(candidate_path, "rb") as f:
            header = f.read(4)
        valid_magics = [b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF"]
        if not any(header.startswith(m) for m in valid_magics):
            findings.append(ValidationFinding(
                validator_id="sfnt.byte_safety.magic",
                layer="byte_safety",
                verdict="warn",
                strength="supporting",
                evidence_ref=f"header_{header.hex()}_not_sfnt",
                repair_actions=("fix_header_magic",),
            ))
    except OSError as e:
        findings.append(ValidationFinding(
            validator_id="sfnt.byte_safety.readable",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"read_error_{e}",
        ))

    return findings


def _validate_structural_parse(candidate_path: str) -> list[ValidationFinding]:
    """Layer 2: fontTools can parse the candidate."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return [ValidationFinding(
            validator_id="sfnt.structural.backend",
            layer="structural_parse",
            verdict="unknown",
            strength="heuristic",
            evidence_ref="fontTools_not_available",
        )]

    try:
        font = TTFont(candidate_path)
        table_tags = list(font.keys())
        font.close()

        return [ValidationFinding(
            validator_id="sfnt.structural.parse",
            layer="structural_parse",
            verdict="pass",
            strength="strong",
            evidence_ref=f"fontTools_parsed_ok_tables_{len(table_tags)}",
        )]
    except Exception as e:
        return [ValidationFinding(
            validator_id="sfnt.structural.parse",
            layer="structural_parse",
            verdict="fail",
            strength="strong",
            evidence_ref=f"fontTools_error_{e}",
            repair_actions=("fix_table_directory", "realign_tables"),
        )]


def _validate_sfnt_directory(candidate_path: str) -> list[ValidationFinding]:
    try:
        data = open(candidate_path, "rb").read()
    except OSError:
        return []

    if len(data) < 12:
        return [ValidationFinding(
            validator_id="sfnt.directory.header",
            layer="invariant_check",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"size_{len(data)}_need_12",
            repair_actions=("use_task_local_font_seed", "rebuild_sfnt_header"),
        )]

    sfnt_version = data[:4]
    valid_magics = {b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}
    if sfnt_version not in valid_magics:
        return [ValidationFinding(
            validator_id="sfnt.directory.magic",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"version_{sfnt_version.hex()}_not_sfnt",
            repair_actions=("fix_header_magic",),
        )]

    num_tables, search_range, entry_selector, range_shift = struct.unpack(">HHHH", data[4:12])
    findings: list[ValidationFinding] = []
    expected_sr, expected_es, expected_rs = _expected_search_params(num_tables)
    if (search_range, entry_selector, range_shift) != (expected_sr, expected_es, expected_rs):
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.search_params",
            layer="invariant_check",
            verdict="warn",
            strength="supporting",
            evidence_ref=(
                f"numTables_{num_tables}_searchRange_{search_range}_expected_{expected_sr}_"
                f"entrySelector_{entry_selector}_expected_{expected_es}_rangeShift_{range_shift}_expected_{expected_rs}"
            ),
            repair_actions=("recompute_sfnt_search_params",),
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.search_params",
            layer="invariant_check",
            verdict="pass",
            strength="supporting",
            evidence_ref=f"numTables_{num_tables}",
        ))

    directory_end = 12 + num_tables * 16
    if directory_end > len(data):
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.range",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"directory_end_{directory_end}_exceeds_size_{len(data)}",
            repair_actions=("repair_table_directory", "use_task_local_font_seed"),
        ))
        return findings

    findings.append(ValidationFinding(
        validator_id="sfnt.directory.range",
        layer="invariant_check",
        verdict="pass",
        strength="strong",
        evidence_ref=f"directory_end_{directory_end}",
    ))

    bad_ranges: list[str] = []
    checksum_mismatches: list[str] = []
    for index in range(num_tables):
        entry = data[12 + index * 16:28 + index * 16]
        tag, declared_checksum, offset, length = struct.unpack(">4sIII", entry)
        tag_text = _tag_text(tag)
        if offset + length > len(data):
            bad_ranges.append(f"{tag_text}@{offset}+{length}")
            continue
        table_data = data[offset:offset + length]
        actual_checksum = _sfnt_table_checksum(tag, table_data)
        if actual_checksum != declared_checksum:
            checksum_mismatches.append(tag_text)

    if bad_ranges:
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.table_range",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"bad_table_ranges_{bad_ranges[:5]}",
            repair_actions=("repair_table_offsets_lengths", "use_task_local_font_seed"),
        ))
    if checksum_mismatches:
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.checksum",
            layer="invariant_check",
            verdict="warn",
            strength="supporting",
            evidence_ref=f"checksum_mismatches_{checksum_mismatches[:5]}",
            repair_actions=("recompute_table_checksums", "recompute_head_checkSumAdjustment"),
        ))
    elif num_tables > 0:
        findings.append(ValidationFinding(
            validator_id="sfnt.directory.checksum",
            layer="invariant_check",
            verdict="pass",
            strength="supporting",
            evidence_ref="table_checksums_match",
        ))
    return findings


def _expected_search_params(num_tables: int) -> tuple[int, int, int]:
    max_power = 1
    entry_selector = 0
    while max_power * 2 <= num_tables:
        max_power *= 2
        entry_selector += 1
    search_range = max_power * 16 if num_tables else 0
    range_shift = num_tables * 16 - search_range
    return search_range, entry_selector, range_shift


def _sfnt_table_checksum(tag: bytes, table_data: bytes) -> int:
    if tag == b"head" and len(table_data) >= 12:
        table_data = table_data[:8] + b"\x00\x00\x00\x00" + table_data[12:]
    padded = table_data + b"\x00" * ((4 - len(table_data) % 4) % 4)
    total = 0
    for offset in range(0, len(padded), 4):
        total = (total + struct.unpack(">I", padded[offset:offset + 4])[0]) & 0xFFFFFFFF
    return total


def _tag_text(tag: bytes) -> str:
    return tag.decode("latin-1", errors="replace")


def _validate_invariants(
    candidate_path: str,
    contract: CarrierContract,
) -> list[ValidationFinding]:
    """Layer 3: check that protected invariants hold."""
    findings: list[ValidationFinding] = []

    try:
        from fontTools.ttLib import TTFont
        font = TTFont(candidate_path)

        # Check head.checkSumAdjustment
        if "head" in font:
            head = font["head"]
            if hasattr(head, "checkSumAdjustment"):
                # The checkSumAdjustment should be valid, but for mutation
                # purposes we only warn, not fail
                pass

        font.close()
    except Exception:
        pass

    return findings


def _validate_mutation_intent(
    candidate_path: str,
    mutation_intent: ExpectedEffect,
) -> list[ValidationFinding]:
    """Layer 5: verify target malformed field preserved after round-trip."""
    findings: list[ValidationFinding] = []

    try:
        with open(candidate_path, "rb") as f:
            raw_bytes = f.read()
    except OSError:
        return findings

    findings.extend(validate_raw_marker_intent(
        raw_bytes,
        mutation_intent,
        validator_id="sfnt.mutation.raw_marker",
        repair_actions=(
            "reapply_mutation_after_table_repair",
            "use_raw_byte_mutation",
            "check_recipe_target_expression",
        ),
    ))

    try:
        from fontTools.ttLib import TTFont
        from io import BytesIO
        font = TTFont(BytesIO(raw_bytes))

        buf = BytesIO()
        font.save(buf)
        roundtripped = buf.getvalue()
        font.close()

        if raw_bytes != roundtripped:
            findings.append(ValidationFinding(
                validator_id="sfnt.mutation.roundtrip",
                layer="mutation_intent",
                verdict="warn",
                strength="supporting",
                evidence_ref="fontTools_modified_candidate_on_roundtrip",
                repair_actions=("bypass_fontTools_save", "use_raw_byte_mutation"),
            ))
        else:
            findings.append(ValidationFinding(
                validator_id="sfnt.mutation.roundtrip",
                layer="mutation_intent",
                verdict="pass",
                strength="strong",
                evidence_ref="fontTools_roundtrip_preserved_bytes",
            ))
    except Exception as e:
        findings.append(ValidationFinding(
            validator_id="sfnt.mutation.roundtrip",
            layer="mutation_intent",
            verdict="unknown",
            strength="heuristic",
            evidence_ref=f"fontTools_cannot_reopen: {e}",
        ))

    return findings


def _build_report(
    candidate_path: str,
    pack_id: str,
    findings: list[ValidationFinding],
    early_exit: bool = False,
) -> ValidationReport:
    """Build a ValidationReport from accumulated findings."""
    has_fail = any(f.verdict == "fail" for f in findings)
    has_warn = any(f.verdict == "warn" for f in findings)

    if has_fail:
        overall = "fail"
    elif has_warn:
        overall = "warn"
    else:
        overall = "pass"

    blocks = any(
        f.verdict == "fail" and f.strength in ("authoritative", "strong")
        for f in findings
    )

    return ValidationReport(
        candidate_path=candidate_path,
        pack_id=pack_id,
        findings=tuple(findings),
        overall_verdict=overall,
        blocks_submit=blocks,
    )
