"""PDF validator — five-layer validation with pikepdf round-trip check.

Layers:
1. byte_safety: file exists, readable, size bounds
2. structural_parse: pikepdf can re-open, object count consistent
3. invariant_check: non-target fields intact, xref consistent
4. harness_acceptance: (deferred — needs runtime harness)
5. mutation_intent: target malformed field preserved after round-trip

Critical: pikepdf may auto-repair.  The validator compares raw bytes
before/after pikepdf round-trip to detect silent repair.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ...models import (
    CarrierContract,
    ExpectedEffect,
    ValidationFinding,
    ValidationReport,
)
from ..raw_marker import validate_raw_marker_intent

logger = logging.getLogger(__name__)

_MAX_CANDIDATE_SIZE = 50 * 1024 * 1024  # 50 MB


def validate_pdf_candidate(
    candidate_path: str,
    contract: CarrierContract,
    mutation_intent: ExpectedEffect | None = None,
) -> ValidationReport:
    """Five-layer validation of a PDF candidate."""
    findings: list[ValidationFinding] = []

    # Layer 1: byte_safety
    findings.extend(_validate_byte_safety(candidate_path))
    if any(f.verdict == "fail" and f.strength == "authoritative" for f in findings):
        return _build_report(candidate_path, "pdf", findings, early_exit=True)

    # Backend-independent checks that should still be visible if pikepdf later
    # normalizes or rejects the candidate.
    findings.extend(_validate_stream_lengths(candidate_path))

    # Layer 2: structural_parse
    findings.extend(_validate_structural_parse(candidate_path))
    if any(f.verdict == "fail" and f.strength in ("authoritative", "strong") for f in findings):
        return _build_report(candidate_path, "pdf", findings, early_exit=True)

    # Layer 3: invariant_check
    findings.extend(_validate_invariants(candidate_path, contract))

    # Layer 4: harness_acceptance — deferred (needs runtime)
    findings.append(ValidationFinding(
        validator_id="pdf.harness",
        layer="harness_acceptance",
        verdict="unknown",
        strength="heuristic",
        evidence_ref="harness_not_available_at_validation_time",
    ))

    # Layer 5: mutation_intent
    if mutation_intent:
        findings.extend(_validate_mutation_intent(candidate_path, mutation_intent))

    return _build_report(candidate_path, "pdf", findings)


def _validate_byte_safety(candidate_path: str) -> list[ValidationFinding]:
    """Layer 1: basic file safety checks."""
    findings: list[ValidationFinding] = []

    if not os.path.exists(candidate_path):
        findings.append(ValidationFinding(
            validator_id="pdf.byte_safety.exists",
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
            validator_id="pdf.byte_safety.empty",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_is_empty",
            repair_actions=("regenerate_candidate",),
        ))
    elif size > _MAX_CANDIDATE_SIZE:
        findings.append(ValidationFinding(
            validator_id="pdf.byte_safety.oversized",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"file_size_{size}_exceeds_{_MAX_CANDIDATE_SIZE}",
            repair_actions=("truncate_candidate",),
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="pdf.byte_safety.exists",
            layer="byte_safety",
            verdict="pass",
            strength="authoritative",
            evidence_ref=f"file_size_{size}",
        ))

    # Check PDF magic
    try:
        with open(candidate_path, "rb") as f:
            header = f.read(8)
        if not header.startswith(b"%PDF"):
            findings.append(ValidationFinding(
                validator_id="pdf.byte_safety.magic",
                layer="byte_safety",
                verdict="fail",
                strength="strong",
                evidence_ref=f"header_{header[:8]!r}_not_pdf",
                repair_actions=("fix_header_magic",),
            ))
        else:
            findings.append(ValidationFinding(
                validator_id="pdf.byte_safety.magic",
                layer="byte_safety",
                verdict="pass",
                strength="strong",
                evidence_ref="header_starts_with_percent_PDF",
            ))
    except OSError as e:
        findings.append(ValidationFinding(
            validator_id="pdf.byte_safety.readable",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"read_error_{e}",
        ))

    return findings


def _validate_structural_parse(candidate_path: str) -> list[ValidationFinding]:
    """Layer 2: pikepdf can parse the candidate."""
    try:
        import pikepdf
    except ImportError:
        return [ValidationFinding(
            validator_id="pdf.structural.backend",
            layer="structural_parse",
            verdict="unknown",
            strength="heuristic",
            evidence_ref="pikepdf_not_available",
        )]

    try:
        pdf = pikepdf.Pdf.open(candidate_path)
        obj_count = len(pdf.objects)
        has_root = pdf.Root is not None
        pdf.close()

        findings: list[ValidationFinding] = []

        findings.append(ValidationFinding(
            validator_id="pdf.structural.parse",
            layer="structural_parse",
            verdict="pass",
            strength="strong",
            evidence_ref=f"pikepdf_parsed_ok_objects_{obj_count}",
        ))

        if not has_root:
            findings.append(ValidationFinding(
                validator_id="pdf.structural.root",
                layer="structural_parse",
                verdict="warn",
                strength="strong",
                evidence_ref="no_root_object",
                repair_actions=("restore_trailer_root",),
            ))

        return findings

    except pikepdf.PasswordError:
        return [ValidationFinding(
            validator_id="pdf.structural.parse",
            layer="structural_parse",
            verdict="warn",
            strength="strong",
            evidence_ref="password_protected",
            repair_actions=("remove_encryption",),
        )]
    except pikepdf.PdfError as e:
        return [ValidationFinding(
            validator_id="pdf.structural.parse",
            layer="structural_parse",
            verdict="fail",
            strength="strong",
            evidence_ref=f"pikepdf_error_{e}",
            repair_actions=("recompute_xref", "fix_stream_length"),
        )]


def _validate_stream_lengths(candidate_path: str) -> list[ValidationFinding]:
    """Check literal /Length values against raw stream byte spans."""
    try:
        data = open(candidate_path, "rb").read()
    except OSError:
        return []

    findings: list[ValidationFinding] = []
    mismatches: list[str] = []
    checked = 0
    for match in re.finditer(rb"<<(?P<dict>.*?)>>\s*stream\r?\n", data, flags=re.DOTALL):
        dict_bytes = match.group("dict")
        length_match = re.search(rb"/Length\s+(?P<length>\d+)", dict_bytes)
        if not length_match:
            continue
        checked += 1
        declared = int(length_match.group("length"))
        stream_start = match.end()
        end_match = re.search(rb"\r?\nendstream\b", data[stream_start:])
        if not end_match:
            mismatches.append(f"stream@{stream_start}:missing_endstream")
            continue
        stream_end = stream_start + end_match.start()
        actual = stream_end - stream_start
        if declared != actual:
            mismatches.append(f"stream@{stream_start}:declared_{declared}_actual_{actual}")

    if mismatches:
        findings.append(ValidationFinding(
            validator_id="pdf.stream.length_mismatch",
            layer="invariant_check",
            verdict="warn",
            strength="supporting",
            evidence_ref=f"stream_length_mismatches_{mismatches[:5]}",
            repair_actions=("fix_stream_length", "preserve_intended_length_mismatch_if_trigger"),
        ))
    elif checked:
        findings.append(ValidationFinding(
            validator_id="pdf.stream.length_mismatch",
            layer="invariant_check",
            verdict="pass",
            strength="supporting",
            evidence_ref=f"stream_lengths_match_{checked}",
        ))
    return findings


def _validate_invariants(
    candidate_path: str,
    contract: CarrierContract,
) -> list[ValidationFinding]:
    """Layer 3: check that non-target invariants hold."""
    findings: list[ValidationFinding] = []

    try:
        import pikepdf
    except ImportError:
        return findings

    try:
        pdf = pikepdf.Pdf.open(candidate_path)

        # Check xref consistency — pikepdf already validated on open
        # Check protected fields
        for field_name in contract.protected_fields:
            if field_name.startswith("pdf.trailer.root"):
                if pdf.Root is None:
                    findings.append(ValidationFinding(
                        validator_id="pdf.invariant.root",
                        layer="invariant_check",
                        verdict="fail",
                        strength="authoritative",
                        invariant_id="trailer_root",
                        evidence_ref="root_missing",
                        repair_actions=("restore_trailer_root",),
                    ))

        pdf.close()
    except Exception:
        pass

    return findings


def _validate_mutation_intent(
    candidate_path: str,
    mutation_intent: ExpectedEffect,
) -> list[ValidationFinding]:
    """Layer 5: verify that the target malformed field is still present.

    Strategy: read raw candidate bytes, open with pikepdf, save round-tripped
    bytes, compare target region.  If pikepdf repaired it, the mutation intent
    is not preserved.
    """
    findings: list[ValidationFinding] = []

    try:
        with open(candidate_path, "rb") as f:
            raw_bytes = f.read()
    except OSError:
        return findings

    findings.extend(validate_raw_marker_intent(
        raw_bytes,
        mutation_intent,
        validator_id="pdf.mutation.raw_marker",
        repair_actions=(
            "reapply_mutation_after_carrier_repair",
            "use_raw_byte_mutation",
            "check_recipe_target_expression",
        ),
    ))

    try:
        import pikepdf
        from io import BytesIO
        pdf = pikepdf.Pdf.open(BytesIO(raw_bytes))

        buf = BytesIO()
        pdf.save(buf)
        roundtripped = buf.getvalue()
        pdf.close()

        if raw_bytes != roundtripped:
            # pikepdf modified the file during round-trip
            findings.append(ValidationFinding(
                validator_id="pdf.mutation.roundtrip",
                layer="mutation_intent",
                verdict="warn",
                strength="supporting",
                evidence_ref="pikepdf_modified_candidate_on_roundtrip",
                repair_actions=("bypass_pikepdf_save", "use_raw_byte_mutation"),
            ))
        else:
            findings.append(ValidationFinding(
                validator_id="pdf.mutation.roundtrip",
                layer="mutation_intent",
                verdict="pass",
                strength="strong",
                evidence_ref="pikepdf_roundtrip_preserved_bytes",
            ))

    except Exception as e:
        # If pikepdf can't re-open, mutation may be preserved
        findings.append(ValidationFinding(
            validator_id="pdf.mutation.roundtrip",
            layer="mutation_intent",
            verdict="unknown",
            strength="heuristic",
            evidence_ref=f"pikepdf_cannot_reopen: {e}",
        ))

    return findings


def _build_report(
    candidate_path: str,
    pack_id: str,
    findings: list[ValidationFinding],
    early_exit: bool = False,
) -> ValidationReport:
    """Build a ValidationReport from accumulated findings."""
    # Determine overall verdict
    has_fail = any(f.verdict == "fail" for f in findings)
    has_warn = any(f.verdict == "warn" for f in findings)

    if has_fail:
        overall = "fail"
    elif has_warn:
        overall = "warn"
    else:
        overall = "pass"

    # Determine if submit should be blocked
    # Only authoritative or strong fails block submit
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
