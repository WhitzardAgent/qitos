"""Shared raw-marker checks for pack mutation-intent validation."""

from __future__ import annotations

import re

from ..models import ExpectedEffect, ValidationFinding

_RAW_MARKER_PREFIXES = (
    "raw_contains:",
    "raw_contains=",
    "bytes_contains:",
    "bytes_contains=",
    "contains:",
    "contains=",
    "pdf.raw_contains:",
    "pdf.raw_contains=",
    "sfnt.raw_contains:",
    "sfnt.raw_contains=",
    "packet.raw_contains:",
    "packet.raw_contains=",
    "image.raw_contains:",
    "image.raw_contains=",
    "tiff.raw_contains:",
    "tiff.raw_contains=",
    "preserve_raw_contains:",
    "preserve_raw_contains=",
)


def validate_raw_marker_intent(
    raw_bytes: bytes,
    mutation_intent: ExpectedEffect,
    *,
    validator_id: str,
    repair_actions: tuple[str, ...],
) -> list[ValidationFinding]:
    """Validate raw-byte markers declared in an ExpectedEffect.

    Recipes can use target_expression/desired_relation/probe fragments such as
    ``raw_contains:%TRIGGER%`` or ``sfnt.raw_contains:hex:00010000``. Missing
    markers are treated as mutation-lost failures.
    """
    findings: list[ValidationFinding] = []
    markers = _extract_raw_markers(mutation_intent)
    for source, marker in markers:
        preview = _bytes_preview(marker)
        if marker in raw_bytes:
            findings.append(ValidationFinding(
                validator_id=validator_id,
                layer="mutation_intent",
                verdict="pass",
                strength="strong",
                evidence_ref=f"{source}_present:{preview}",
            ))
        else:
            findings.append(ValidationFinding(
                validator_id=validator_id,
                layer="mutation_intent",
                verdict="fail",
                strength="strong",
                evidence_ref=f"mutation_lost_missing_{source}:{preview}",
                repair_actions=repair_actions,
            ))
    return findings


def _extract_raw_markers(mutation_intent: ExpectedEffect) -> tuple[tuple[str, bytes], ...]:
    markers: list[tuple[str, bytes]] = []
    fields = (
        ("target_expression", mutation_intent.target_expression),
        ("desired_relation", mutation_intent.desired_relation),
        ("expected_runtime_probe", mutation_intent.expected_runtime_probe),
    )
    for source, text in fields:
        for chunk in _split_intent_text(text):
            marker = _parse_raw_marker(chunk)
            if marker:
                markers.append((source, marker))
    return tuple(markers)


def _split_intent_text(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    return tuple(part.strip() for part in re.split(r"[;\n]", text) if part.strip())


def _parse_raw_marker(text: str) -> bytes | None:
    lowered = text.lower()
    for prefix in _RAW_MARKER_PREFIXES:
        if lowered.startswith(prefix):
            raw_value = text[len(prefix):].strip()
            if not raw_value:
                return None
            return _marker_to_bytes(raw_value)
    return None


def _marker_to_bytes(raw_value: str) -> bytes | None:
    value = raw_value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]

    if value.lower().startswith("hex:"):
        try:
            return bytes.fromhex(value[4:].strip())
        except ValueError:
            return None
    return value.encode("utf-8")


def _bytes_preview(value: bytes) -> str:
    if len(value) <= 32:
        return repr(value)
    return f"{value[:32]!r}...len_{len(value)}"
