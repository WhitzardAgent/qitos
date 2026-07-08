"""Small deterministic solver for extracted numeric constraints."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def solve_constraints(constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    solutions: list[dict[str, Any]] = []
    for constraint in constraints[:16]:
        if not isinstance(constraint, dict):
            continue
        assignments: list[dict[str, Any]] = []
        for value in list(constraint.get("candidate_values") or [])[:4]:
            field = str(value.get("field") or value.get("name") or "")
            if not field:
                continue
            assignments.append({
                "field": field,
                "value": _parse_value(value.get("value")),
                "reason": str(value.get("reason") or constraint.get("kind") or ""),
            })
        if not assignments:
            for field in list(constraint.get("input_fields") or [])[:4]:
                name = str(field.get("name") or field.get("field") or "")
                if not name:
                    continue
                assignments.append({
                    "field": name,
                    "value": _default_value(str(constraint.get("kind") or ""), int(field.get("width") or 4)),
                    "reason": str(constraint.get("formula") or ""),
                })
        material = f"{constraint.get('constraint_id')}|{assignments}"
        solutions.append({
            "solution_id": "csol_" + hashlib.blake2s(material.encode(), digest_size=5).hexdigest(),
            "constraint_id": str(constraint.get("constraint_id") or ""),
            "kind": str(constraint.get("kind") or ""),
            "formula": str(constraint.get("formula") or ""),
            "assignments": assignments,
            "status": "ready" if assignments else "needs_field_localization",
            "open_gaps": [] if assignments else ["needs_field_localization: no candidate assignment"],
        })
    return solutions


def _parse_value(value: Any) -> int:
    raw = str(value or "0").strip().lower()
    if "+" in raw:
        total = 0
        for part in raw.split("+"):
            total += _parse_value(part)
        return total
    m = re.search(r'0x[0-9a-f]+|\d+', raw)
    return int(m.group(0), 0) if m else 0


def _default_value(kind: str, width: int) -> int:
    max_val = (1 << (max(width, 1) * 8)) - 1
    if kind in {"overflow", "signedness"}:
        return max_val
    if kind == "underflow":
        return 0
    if kind == "boundary":
        return min(max_val, 1024)
    return max_val
