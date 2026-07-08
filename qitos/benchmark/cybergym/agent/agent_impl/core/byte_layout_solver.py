"""Compile solved numeric constraints into structured byte mutations."""

from __future__ import annotations

from typing import Any


def compile_solution_to_mutations(
    solution: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    mutations: list[dict[str, Any]] = []
    gaps: list[str] = []
    for assignment in list(solution.get("assignments") or []):
        field = str(assignment.get("field") or "")
        mapping = _match_mapping(field, mappings)
        if not mapping:
            gaps.append(f"needs_field_localization: constraint field {field} has no input mapping")
            continue
        offset = mapping.get("offset")
        width = mapping.get("width") or _guess_width(assignment.get("value"))
        if offset is None:
            gaps.append(f"needs_field_localization: constraint field {field} offset is unknown")
            continue
        mutations.append({
            "mapping_id": mapping.get("mapping_id", ""),
            "constraint_solution_id": solution.get("solution_id", ""),
            "argument_role": mapping.get("argument_role", "constraint"),
            "value_strategy": "constraint_assignment",
            "action": f"set {field}={assignment.get('value')}",
            "field": field,
            "offset": int(offset),
            "width": int(width),
            "value": int(assignment.get("value") or 0),
            "endian": mapping.get("endian", "big"),
            "executable": True,
        })
    return {"mutations": mutations, "open_gaps": gaps}


def _match_mapping(field: str, mappings: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = field.lower()
    for mapping in mappings:
        text = " ".join(
            str(mapping.get(key) or "")
            for key in ("field", "name", "source_field", "sink_expression", "argument_role", "mapping_id")
        ).lower()
        if needle and needle in text:
            return mapping
    return None


def _guess_width(value: Any) -> int:
    numeric = int(value or 0)
    if numeric <= 0xFF:
        return 1
    if numeric <= 0xFFFF:
        return 2
    return 4
