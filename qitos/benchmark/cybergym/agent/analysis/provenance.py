"""Input provenance builder — infers InputByteMapping dicts from
ranked paths, mechanism graphs, and harness consumption models.

Produces compact input-to-sink mappings for recipe generation.
Prioritizes source-backed mappings; falls back to unresolved placeholders.
"""

from __future__ import annotations

import hashlib
from typing import Any


def infer_input_mappings_for_path(
    *,
    ranked_path: dict[str, Any],
    mechanism_graph: dict[str, Any] | None = None,
    harness: dict[str, Any] | None = None,
    existing_mappings: list[dict[str, Any]] | None = None,
    description_analysis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Infer compact input-to-sink mappings for recipe generation.

    Preserves source-backed mappings from existing_mappings; adds
    unresolved placeholders for unmapped roles.
    """
    results: list[dict[str, Any]] = []
    path_id = str(ranked_path.get("path_id") or "")
    endpoint = ranked_path.get("endpoint") or {}
    endpoint_func = str(endpoint.get("function", ""))

    # Preserve existing source-backed mappings for this path
    existing = existing_mappings or []
    for m in existing:
        if m.get("ranked_path_id") == path_id or not m.get("ranked_path_id"):
            if m.get("status") not in ("unresolved", ""):
                results.append(m)

    # If we have enough resolved mappings, don't add placeholders
    resolved_count = sum(1 for m in results if m.get("status") not in ("unresolved", "needs_field_localization", ""))
    if resolved_count >= 3:
        return results[:8]

    # Determine what roles need mapping
    mechanism_family = (mechanism_graph or {}).get("mechanism_family", "")
    missing_roles = (mechanism_graph or {}).get("missing_roles", [])
    nodes = (mechanism_graph or {}).get("nodes", [])

    # Build role-to-argument mappings
    roles_needed = _infer_needed_roles(mechanism_family, nodes, description_analysis)

    for role_info in roles_needed:
        # Check if already covered by existing mapping
        role = role_info["argument_role"]
        if any(m.get("argument_role") == role and m.get("ranked_path_id") == path_id for m in results):
            continue

        mapping_id = _stable_mapping_id(path_id, role)
        mapping = {
            "mapping_id": mapping_id,
            "sink_argument": role_info.get("sink_argument", ""),
            "sink_expression": role_info.get("sink_expression", ""),
            "source_parameter": role_info.get("source_parameter", ""),
            "offset_expression": "",
            "offset": None,
            "width": None,
            "endianness": "unknown",
            "transform": "",
            "constraint": role_info.get("constraint", ""),
            "status": "needs_field_localization",
            "confidence": 0.3,
            "evidence": [],
            "gaps": [{"reason": f"{role} field offset unknown"}],
            "argument_role": role,
            "value_strategy": role_info.get("value_strategy", ""),
            "ranked_path_id": path_id,
        }

        # If harness provides selector/magic info, fill it in
        if harness:
            consumption = harness.get("consumption") or {}
            if role == "selector" and consumption.get("selector_expression"):
                mapping["offset_expression"] = consumption["selector_expression"]
                mapping["status"] = "inferred"
            if role == "length" and consumption.get("size_parameter"):
                mapping["source_parameter"] = consumption["size_parameter"]

        results.append(mapping)

    return results[:8]


def _stable_mapping_id(path_id: str, role: str) -> str:
    material = f"{path_id}|{role}"
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"map_{h}"


def _infer_needed_roles(
    family: str,
    nodes: list[dict[str, Any]],
    desc: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Determine what input roles are needed based on mechanism family."""
    roles: list[dict[str, Any]] = []

    if family in ("heap_oob", "global_oob", "stack_oob"):
        roles.append({
            "argument_role": "length",
            "sink_argument": "size/length parameter",
            "sink_expression": "declared_len vs allocated_len",
            "source_parameter": "input length field",
            "value_strategy": "oversize",
            "constraint": "declared length > allocation",
        })
        # Check for index role
        access_nodes = [n for n in nodes if n.get("role") == "access"]
        for n in access_nodes:
            idx = n.get("index_expr", "")
            if idx and ("-" in idx or "signed" in idx.lower()):
                roles.append({
                    "argument_role": "index",
                    "sink_argument": "index parameter",
                    "sink_expression": idx,
                    "source_parameter": "input index field",
                    "value_strategy": "negative",
                    "constraint": "index < 0 or wrap-around",
                })
                break

    elif family == "origin_use":
        roles.append({
            "argument_role": "pointer",
            "sink_argument": "uninitialized variable",
            "sink_expression": "field used before set",
            "source_parameter": "input that skips initialization path",
            "value_strategy": "null_or_stale",
            "constraint": "avoid initialization branch",
        })

    elif family in ("use_after_free", "double_free"):
        roles.append({
            "argument_role": "state",
            "sink_argument": "freed pointer/state",
            "sink_expression": "access after free/invalidation",
            "source_parameter": "input triggering state transition",
            "value_strategy": "duplicate_free_sequence",
            "constraint": "trigger free then reuse",
        })

    else:
        # Generic: need at least a length mapping
        roles.append({
            "argument_role": "length",
            "sink_argument": "size parameter",
            "sink_expression": "buffer size control",
            "source_parameter": "input",
            "value_strategy": "oversize",
            "constraint": "",
        })

    # Always add a selector role if description mentions dispatch/mode
    if desc:
        tags = [t.lower() for t in (desc.get("mechanism_tags") or [])]
        if any("dispatch" in t or "selector" in t or "mode" in t for t in tags):
            roles.append({
                "argument_role": "selector",
                "sink_argument": "dispatch selector",
                "sink_expression": "mode/arch/protocol selector",
                "source_parameter": "input selector field",
                "value_strategy": "choose_case",
                "constraint": "select vulnerable case",
            })

    return roles
