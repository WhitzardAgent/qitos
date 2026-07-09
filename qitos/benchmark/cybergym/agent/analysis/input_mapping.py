"""Derive compact sink-argument → input-byte mappings from source traces."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .models import InputByteMapping, SourceLocation, stable_value


def derive_input_mapping(
    trace: dict[str, Any],
    *,
    harness: Any = None,
    sink_argument: str,
    sink_expression: str,
    constraint: str = "",
) -> InputByteMapping:
    """Conservatively derive an input byte mapping.

    Unknown offset/width/endianness remain explicit.  This function never
    guesses endian from variable names or coerces symbolic offsets to ints.
    """
    expression = str(sink_expression or trace.get("expression") or "")
    source_param = _source_parameter(trace, expression, harness)
    evidence = _trace_evidence(trace)
    offset_expr = ""
    offset: int | None = None
    width: int | None = None
    endian = "unknown"
    transform = ""
    status = "unresolved"
    confidence = 0.0
    gaps: list[dict[str, Any]] = []

    match = re.search(rf"\b{re.escape(source_param or 'data')}\s*\[\s*(\d+)\s*\]", expression)
    if match:
        offset = int(match.group(1))
        offset_expr = str(offset)
        width = 1
        status = "confirmed"
        confidence = .90
    if status == "unresolved":
        helper = re.search(
            rf"\b(read|load|get)_?(le|be)?(16|32|64)\s*\(\s*(?:{re.escape(source_param or 'data')})\s*(?:\+\s*([^)]+?))?\s*\)",
            expression,
            re.IGNORECASE,
        )
        if helper:
            endian = {"le": "little", "be": "big"}.get((helper.group(2) or "").lower(), "unknown")
            width = int(helper.group(3)) // 8
            offset_expr = (helper.group(4) or "0").strip()
            offset = _int_or_none(offset_expr)
            transform = helper.group(0).split("(", 1)[0]
            status = "confirmed" if endian != "unknown" else "inferred"
            confidence = .86 if endian != "unknown" else .72
    if status == "unresolved":
        cast = re.search(
            rf"\*\s*\(\s*(?:u?int(16|32|64)_t|(?:unsigned\s+)?(?:short|int|long))\s*\*\s*\)\s*\(?\s*{re.escape(source_param or 'data')}\s*(?:\+\s*([^)]+))?\)?",
            expression,
            re.IGNORECASE,
        )
        if cast:
            bits = int(cast.group(1) or 32)
            width = bits // 8
            offset_expr = (cast.group(2) or "0").strip()
            offset = _int_or_none(offset_expr)
            transform = "native_cast"
            status = "inferred"
            confidence = .62
    if status == "unresolved":
        pointer = re.search(rf"\b{re.escape(source_param or 'data')}\s*\+\s*([^,;)]+)", expression)
        if pointer:
            offset_expr = pointer.group(1).strip()
            offset = _int_or_none(offset_expr)
            status = "inferred"
            confidence = .55
    if status == "unresolved":
        gaps.append({
            "id": "input_mapping_unresolved",
            "reason": "expression could not be traced to a concrete or symbolic input byte range",
        })

    material = repr(stable_value({
        "arg": sink_argument,
        "expr": expression,
        "source": source_param,
        "offset": offset_expr,
        "constraint": constraint,
    }))
    return InputByteMapping(
        mapping_id="imap_" + hashlib.blake2s(material.encode(), digest_size=7).hexdigest(),
        sink_argument=sink_argument,
        sink_expression=expression,
        source_parameter=source_param,
        offset_expression=offset_expr,
        offset=offset,
        width=width,
        endianness=endian,
        transform=transform,
        constraint=constraint,
        status=status,
        confidence=confidence,
        evidence=evidence,
        gaps=gaps,
    )


def _source_parameter(trace: dict[str, Any], expression: str, harness: Any) -> str:
    if "data" in expression:
        return "data"
    if "Data" in expression:
        return "Data"
    value = ""
    if isinstance(harness, dict):
        consumption = harness.get("consumption") or harness
        value = str(consumption.get("data_parameter") or "")
    else:
        value = str(getattr(harness, "data_parameter", "") or "")
    return value or "data"


def _trace_evidence(trace: dict[str, Any]) -> list[SourceLocation]:
    result: list[SourceLocation] = []
    for step in list(trace.get("trace") or trace.get("steps") or [])[:3]:
        loc = step.get("location") if isinstance(step, dict) else None
        if isinstance(loc, dict) and loc.get("file"):
            result.append(SourceLocation(
                str(loc.get("file") or ""),
                int(loc.get("start_line") or loc.get("line") or 0),
                int(loc.get("start_column") or 1),
                int(loc.get("end_line") or loc.get("start_line") or 0),
                int(loc.get("end_column") or 1),
            ))
    return result


def _int_or_none(value: str) -> int | None:
    try:
        if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", value.strip()):
            return int(value, 0)
    except Exception:
        pass
    return None
