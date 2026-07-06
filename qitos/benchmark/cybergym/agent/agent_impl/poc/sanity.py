"""PoC sanity checker — validate PoC files before submit.

Three check layers:
1. Generic bytes: non-empty, min-size, magic, offset bounds
2. Corpus-aware: seed comparison, delta summary
3. Format-aware: PNG/JPEG/PDF/ZIP/WAV/BMP/font/SFNT/OTF/CFF2 carrier sanity

Only FAIL blocks obviously invalid PoCs (empty, wrong magic, table directory OOB).
WARN does not block submit — PoC is intentionally malformed in fuzzing contexts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .sanity_formats import (
    check_av1,
    check_bmp,
    check_font,
    check_jpeg,
    check_pdf,
    check_png,
    check_tiff,
    check_wav,
    check_zip,
    check_zstd,
)


@dataclass
class PoCSanityIssue:
    severity: Literal["fail", "warn", "info"]
    category: Literal["magic", "size", "offset", "field", "corpus_delta", "format", "font_table"]
    message: str
    evidence: str = ""
    repair_hint: str = ""


@dataclass
class PoCSanityResult:
    path: str
    expected_format: str
    passed: bool
    issues: list[PoCSanityIssue] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "expected_format": self.expected_format,
            "passed": self.passed,
            "summary": self.summary,
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "evidence": i.evidence,
                    "repair_hint": i.repair_hint,
                }
                for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Magic-byte lookup
# ---------------------------------------------------------------------------

_FORMAT_MAGIC: list[tuple[bytes, str, int]] = [
    # (magic_prefix, format_name, min_header_bytes)
    (b"\x89PNG\r\n\x1a\n", "png", 8),
    (b"\xff\xd8\xff", "jpeg", 3),
    (b"BM", "bmp", 2),
    (b"%PDF", "pdf", 4),
    (b"PK\x03\x04", "zip", 4),
    (b"RIFF", "wav", 4),
    (b"\x00\x01\x00\x00", "font/ttf", 4),
    (b"OTTO", "font/otf", 4),
    (b"true", "font/ttf", 4),
    (b"typ1", "font/ttf", 4),
    (b"ttcf", "font/ttc", 4),
    (b"wOFF", "font/woff", 4),
    (b"wOF2", "font/woff2", 4),
    # TIFF: both endian marks
    (b"II\x2a\x00", "tiff", 4),  # little-endian
    (b"MM\x00\x2a", "tiff", 4),  # big-endian
    # Zstandard
    (b"\x28\xb5\x2f\xfd", "zstd", 4),
]


def _detect_format(data: bytes) -> str:
    """Detect format from magic bytes. Returns '' if unknown."""
    for magic, fmt, _min_bytes in _FORMAT_MAGIC:
        if len(data) >= len(magic) and data[: len(magic)] == magic:
            return fmt
    return ""


# ---------------------------------------------------------------------------
# Layer 1: Generic byte checks
# ---------------------------------------------------------------------------


def _check_generic(data: bytes, path: str, issues: list[PoCSanityIssue]) -> str:
    """Generic checks: non-empty, size, magic detection. Returns detected format."""
    if len(data) == 0:
        issues.append(PoCSanityIssue(
            severity="fail", category="size",
            message="PoC file is empty",
            repair_hint="Write non-empty content to the PoC file.",
        ))
        return ""

    if len(data) < 4:
        issues.append(PoCSanityIssue(
            severity="warn", category="size",
            message=f"PoC file is very small ({len(data)} bytes)",
            evidence=f"size={len(data)}",
        ))
        return ""

    detected = _detect_format(data)
    return detected


# ---------------------------------------------------------------------------
# Layer 2: Corpus-aware checks
# ---------------------------------------------------------------------------


def _check_corpus(
    data: bytes,
    seed_path: str | None,
    issues: list[PoCSanityIssue],
) -> None:
    """Compare PoC against seed: delta size, outer magic/container consistency."""
    if not seed_path:
        return

    seed = Path(seed_path)
    if not seed.is_file():
        issues.append(PoCSanityIssue(
            severity="info", category="corpus_delta",
            message="Seed file not found for comparison",
            evidence=f"seed_path={seed_path}",
        ))
        return

    seed_data = seed.read_bytes()
    if len(seed_data) == 0:
        return

    # Check outer magic consistency
    if len(data) >= 4 and len(seed_data) >= 4:
        poc_magic = data[:4]
        seed_magic = seed_data[:4]
        if poc_magic != seed_magic:
            issues.append(PoCSanityIssue(
                severity="warn", category="magic",
                message="PoC magic bytes differ from seed",
                evidence=f"poc={poc_magic.hex()} seed={seed_magic.hex()}",
                repair_hint="If mutating a seed, preserve the outer container magic bytes.",
            ))

    # Compute delta
    min_len = min(len(data), len(seed_data))
    changed = sum(1 for i in range(min_len) if data[i] != seed_data[i])
    delta_pct = changed / max(len(seed_data), 1) * 100

    if delta_pct > 50:
        issues.append(PoCSanityIssue(
            severity="warn", category="corpus_delta",
            message=f"Large delta from seed: {delta_pct:.0f}% bytes changed",
            evidence=f"changed={changed}/{len(seed_data)} delta_pct={delta_pct:.1f}",
            repair_hint="Large delta may break the carrier skeleton. Prefer targeted mutation at specific offsets.",
        ))
    elif delta_pct > 0:
        issues.append(PoCSanityIssue(
            severity="info", category="corpus_delta",
            message=f"Delta from seed: {delta_pct:.0f}% bytes changed ({changed} bytes)",
            evidence=f"changed={changed}/{len(seed_data)}",
        ))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def inspect_poc_bytes(
    path: str,
    *,
    expected_format: str = "",
    seed_path: str | None = None,
    state: Any | None = None,
) -> PoCSanityResult:
    """Inspect a PoC file for carrier sanity issues.

    Returns a PoCSanityResult with pass/warn/fail verdict.
    - FAIL: blocks obviously invalid PoC (empty, wrong magic, table directory OOB)
    - WARN: does not block submit

    When state is provided, also runs five-layer knowledge pack validation
    if a confirmed pack exists for the format.
    """
    issues: list[PoCSanityIssue] = []
    p = Path(path)

    # Read file
    if not p.is_file():
        issues.append(PoCSanityIssue(
            severity="fail", category="size",
            message=f"PoC file does not exist: {path}",
        ))
        return PoCSanityResult(
            path=path, expected_format=expected_format,
            passed=False, issues=issues,
            summary="FAIL: file not found",
        )

    data = p.read_bytes()

    # Layer 1: generic checks
    detected_format = _check_generic(data, path, issues)

    # Determine format for format-aware checks
    effective_format = expected_format or detected_format

    # Magic mismatch check
    if expected_format and detected_format:
        # Normalize for comparison
        fmt_map = {
            "font": "font", "font/ttf": "font", "font/otf": "font",
            "font/ttc": "font", "font/woff": "font", "font/woff2": "font",
        }
        exp_normalized = fmt_map.get(expected_format, expected_format)
        det_normalized = fmt_map.get(detected_format, detected_format)
        if exp_normalized != det_normalized and exp_normalized != "":
            issues.append(PoCSanityIssue(
                severity="fail", category="magic",
                message=f"PoC magic indicates {detected_format} but expected {expected_format}",
                evidence=f"detected={detected_format} expected={expected_format}",
                repair_hint=f"Ensure PoC starts with correct {expected_format} magic bytes.",
            ))

    # Layer 2: corpus-aware checks
    _check_corpus(data, seed_path, issues)

    # Layer 3: format-aware checks (existing)
    if effective_format:
        fmt_key = effective_format.split("/")[0] if "/" in effective_format else effective_format
        if fmt_key == "font" or effective_format.startswith("font/"):
            check_font(data, effective_format, issues)
        elif fmt_key == "png":
            check_png(data, issues)
        elif fmt_key == "jpeg":
            check_jpeg(data, issues)
        elif fmt_key == "bmp":
            check_bmp(data, issues)
        elif fmt_key == "pdf":
            check_pdf(data, issues)
        elif fmt_key == "zip":
            check_zip(data, issues)
        elif fmt_key == "wav":
            check_wav(data, issues)
        elif fmt_key == "tiff":
            check_tiff(data, issues)
        elif fmt_key == "av1":
            check_av1(data, issues)
        elif fmt_key == "zstd":
            check_zstd(data, issues)

    # Layer 3.5: Knowledge pack five-layer validation (if state available)
    if state is not None:
        try:
            from ..knowledge.validation import validate_with_knowledge_pack, merge_pack_findings
            pack_report = validate_with_knowledge_pack(path, state)
            if pack_report is not None:
                result = PoCSanityResult(
                    path=path,
                    expected_format=effective_format,
                    passed=True,
                    issues=issues,
                    summary="",
                )
                result = merge_pack_findings(result, pack_report)
                issues = result.issues
        except Exception:
            pass  # Pack validation is supplementary — never crash

    # Determine verdict
    has_fail = any(i.severity == "fail" for i in issues)
    warn_count = sum(1 for i in issues if i.severity == "warn")
    info_count = sum(1 for i in issues if i.severity == "info")

    if has_fail:
        summary = f"FAIL: {sum(1 for i in issues if i.severity == 'fail')} critical issue(s)"
    elif warn_count > 0:
        summary = f"WARN: {warn_count} warning(s), {info_count} info"
    else:
        summary = f"PASS: {info_count} info" if info_count else "PASS"

    return PoCSanityResult(
        path=path,
        expected_format=expected_format,
        passed=not has_fail,
        issues=issues,
        summary=summary,
    )
