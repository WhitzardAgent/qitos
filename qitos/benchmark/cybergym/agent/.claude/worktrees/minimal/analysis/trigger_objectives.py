"""Trigger objective builder — converts mechanism graphs and input mappings
into actionable TriggerObjective dicts.

Each objective tells the agent "what the next PoC should satisfy to be
observable by the sanitizer/oracle", not just "this function is suspicious".
"""

from __future__ import annotations

import hashlib
from typing import Any


def build_trigger_objectives(
    *,
    ranked_paths: list[dict[str, Any]],
    mechanism_graphs: list[dict[str, Any]],
    input_mappings: list[dict[str, Any]],
    crash_type: str = "",
    description_analysis: dict[str, Any] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Build TriggerObjective dicts for active paths.

    Returns at most top_k objectives.
    """
    results: list[dict[str, Any]] = []
    graph_by_path = {g.get("ranked_path_id", ""): g for g in mechanism_graphs}

    for path in ranked_paths[:top_k]:
        path_id = str(path.get("path_id") or "")
        if not path_id:
            continue
        graph = graph_by_path.get(path_id)
        mappings_for_path = [
            m for m in input_mappings
            if m.get("ranked_path_id") == path_id or not m.get("ranked_path_id")
        ]
        obj = _build_single_objective(
            path=path,
            graph=graph,
            mappings=mappings_for_path,
            crash_type=crash_type,
            description_analysis=description_analysis,
        )
        if obj:
            results.append(obj)

    return results


def _stable_objective_id(path_id: str, kind: str, idx: int) -> str:
    material = f"{path_id}|{kind}|{idx}"
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"obj_{h}"


def _build_single_objective(
    *,
    path: dict[dict[str, Any], Any],
    graph: dict[str, Any] | None,
    mappings: list[dict[str, Any]],
    crash_type: str,
    description_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build one trigger objective from a path + graph."""
    path_id = str(path.get("path_id") or "")
    endpoint = path.get("endpoint") or {}
    endpoint_func = str(endpoint.get("function") or "")
    endpoint_file = str(endpoint.get("file") or "")
    endpoint_line = int(endpoint.get("line", 0) or 0)

    family = str(path.get("candidate_family") or "unknown")
    graph_family = (graph or {}).get("mechanism_family", "")

    # Determine objective kind from mechanism family
    kind = _objective_kind_from_family(graph_family, crash_type, description_analysis)

    # Determine oracle kind
    oracle_kind, oracle_signal = _oracle_kind_from_crash_type(crash_type, description_analysis)

    # Observable: what the sanitizer should show
    observable = _build_observable(kind, oracle_kind, endpoint_func, crash_type)

    # Required conditions
    required_conditions = _build_required_conditions(kind, graph, mappings, description_analysis)

    # Input fields from mappings
    input_fields = _build_input_fields(mappings, kind)

    # Anti-goals
    anti_goals = _build_anti_goals(kind, family, description_analysis)

    # Preconditions
    preconditions = _build_preconditions(kind, description_analysis)

    objective_id = _stable_objective_id(path_id, kind, 0)

    # Determine initial no_trigger_diagnosis based on oracle_kind
    no_trigger_diagnosis = _infer_no_trigger_diagnosis(
        oracle_kind, kind, crash_type, description_analysis,
    )

    return {
        "objective_id": objective_id,
        "ranked_path_id": path_id,
        "mechanism_graph_id": (graph or {}).get("graph_id", ""),
        "kind": kind,
        "target_function": endpoint_func,
        "target_file": endpoint_file,
        "target_line": endpoint_line,
        "observable": observable,
        "required_conditions": required_conditions,
        "input_fields": input_fields,
        "preconditions": preconditions,
        "anti_goals": anti_goals,
        "oracle_kind": oracle_kind,
        "oracle_signal": oracle_signal,
        "no_trigger_diagnosis": no_trigger_diagnosis,
        "confidence": float(path.get("score", 0.5)),
        "status": "active",
    }


def _objective_kind_from_family(
    family: str,
    crash_type: str,
    desc: dict[str, Any] | None,
) -> str:
    if family in ("heap_oob", "global_oob", "stack_oob"):
        return "bounds"
    if family == "origin_use":
        return "origin_use"
    if family in ("use_after_free", "double_free"):
        return "lifetime"
    # Fallback from crash_type
    ct = (crash_type or "").lower()
    if "overflow" in ct or "oob" in ct:
        return "bounds"
    if "uninitial" in ct:
        return "origin_use"
    if "use-after-free" in ct or "double-free" in ct:
        return "lifetime"
    # Check description
    if desc:
        tags = [t.lower() for t in (desc.get("mechanism_tags") or [])]
        if any("oob" in t or "overflow" in t for t in tags):
            return "bounds"
        if any("uninitial" in t for t in tags):
            return "origin_use"
        if any("use_after_free" in t or "uaf" in t for t in tags):
            return "lifetime"
    return "bounds"  # default


def _oracle_kind_from_crash_type(
    crash_type: str,
    desc: dict[str, Any] | None,
) -> tuple[str, str]:
    ct = (crash_type or "").lower()
    if "msan" in ct or "use-of-uninitialized" in ct or "uninitial" in ct:
        return "msan", "use-of-uninitialized-value"
    if "ubsan" in ct:
        return "ubsan", "undefined-behavior"
    if "leak" in ct:
        return "leak", "memory-leak"
    if "asan" in ct or "heap-buffer" in ct or "stack-buffer" in ct or "use-after-free" in ct:
        return "asan", ct
    # Default: assume ASAN
    return "asan", ct


def _build_observable(
    kind: str,
    oracle_kind: str,
    endpoint_func: str,
    crash_type: str,
) -> str:
    if kind == "bounds":
        return f"ASAN stack includes {endpoint_func} access site"
    if kind == "origin_use":
        return f"MSan use-of-uninitialized-value in {endpoint_func}"
    if kind == "lifetime":
        return f"ASAN heap-use-after-free/double-free in {endpoint_func}"
    return f"{oracle_kind.upper()} signal in {endpoint_func}"


def _build_required_conditions(
    kind: str,
    graph: dict[str, Any] | None,
    mappings: list[dict[str, Any]],
    desc: dict[str, Any] | None,
) -> list[str]:
    conditions: list[str] = []

    if kind == "bounds":
        # From graph: allocation vs access size mismatch
        if graph:
            missing = graph.get("missing_roles", [])
            if "guard" in missing:
                conditions.append("no bounds guard between allocation and access")
            alloc_nodes = [n for n in graph.get("nodes", []) if n.get("role") == "allocation"]
            if alloc_nodes:
                size_expr = alloc_nodes[0].get("size_expr", "")
                if size_expr:
                    conditions.append(f"declared/access size exceeds allocated ({size_expr})")
        # From mappings: length/size field
        length_mappings = [m for m in mappings if m.get("argument_role") == "length"]
        if length_mappings:
            conditions.append("input length field value exceeds allocation")
        if not conditions:
            conditions.append("reach the vulnerable function with oversized input")

    elif kind == "origin_use":
        conditions.append("leave field uninitialized while reaching use site")
        conditions.append("avoid early exit that would set the field")
        if graph:
            missing = graph.get("missing_roles", [])
            if "sanitizer_origin" in missing:
                conditions.append("identify which field/variable is left uninitialized")

    elif kind == "lifetime":
        conditions.append("trigger free/invalidation then subsequent access")
        conditions.append("avoid null-check that would prevent the access")
        if graph:
            missing = graph.get("missing_roles", [])
            if "free" in missing:
                conditions.append("identify the free/invalidation point")

    # Add description-derived conditions
    if desc:
        trigger_conds = desc.get("trigger_conditions") or []
        for tc in trigger_conds[:2]:
            if tc and tc not in conditions:
                conditions.append(tc)

    return conditions[:5]


def _build_input_fields(
    mappings: list[dict[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen_roles: set[str] = set()

    for m in mappings:
        role = m.get("argument_role", "")
        if role in seen_roles:
            continue
        seen_roles.add(role)
        field = {
            "field": m.get("sink_argument", ""),
            "argument_role": role,
            "value_strategy": m.get("value_strategy", ""),
            "source": f"input_mapping:{m.get('mapping_id', '')}",
        }
        offset = m.get("offset")
        if offset is not None:
            field["offset"] = offset
        width = m.get("width")
        if width is not None:
            field["width"] = width
        status = m.get("status", "unresolved")
        if status:
            field["status"] = status
        fields.append(field)

    # If no mappings, add placeholder
    if not fields:
        if kind == "bounds":
            fields.append({
                "field": "input_length",
                "argument_role": "length",
                "value_strategy": "oversize",
                "status": "needs_field_localization",
            })
        elif kind == "origin_use":
            fields.append({
                "field": "uninitialized_field",
                "argument_role": "pointer",
                "value_strategy": "null_or_stale",
                "status": "needs_field_localization",
            })
        elif kind == "lifetime":
            fields.append({
                "field": "freed_pointer",
                "argument_role": "pointer",
                "value_strategy": "null_or_stale",
                "status": "needs_field_localization",
            })

    return fields[:4]


def _build_anti_goals(
    kind: str,
    family: str,
    desc: dict[str, Any] | None,
) -> list[str]:
    goals: list[str] = []

    # Common anti-goals
    goals.append("do not corrupt file magic/header before parser dispatch")

    if kind == "bounds":
        goals.append("do not trigger early return/validation before reaching the access")
    elif kind == "origin_use":
        goals.append("do not initialize the field in the path you control")
    elif kind == "lifetime":
        goals.append("do not null-check the pointer after free")

    return goals[:3]


def _build_preconditions(
    kind: str,
    desc: dict[str, Any] | None,
) -> list[str]:
    preconds: list[str] = []

    if desc:
        ops = desc.get("described_operations") or []
        for op in ops[:2]:
            if op:
                preconds.append(f"reach {op}")

    return preconds[:3]


def _infer_no_trigger_diagnosis(
    oracle_kind: str,
    kind: str,
    crash_type: str,
    desc: dict[str, Any] | None,
) -> str:
    """Infer an initial no-trigger diagnosis for the objective.

    Values: path_not_reached | oracle_not_observable | trigger_condition_unmet
            | wrong_harness | unknown

    This is set at build time and can be updated later by feedback arbitration
    when actual submit results come in.
    """
    # MSan objectives: the oracle may not be observable if the harness
    # binary is not built with MSan instrumentation.
    if oracle_kind == "msan":
        return "oracle_not_observable"

    # Semantic/logic oracle: the bug may not produce a crash at all,
    # only a wrong output or state.
    if oracle_kind in ("semantic_accept", "parser_reach"):
        return "oracle_not_observable"

    # Lifetime bugs in harnesses without ASan fake-stack support
    if kind == "lifetime" and oracle_kind == "asan":
        ct = (crash_type or "").lower()
        if "stack-use-after-return" in ct or "stack-use-after-scope" in ct:
            return "oracle_not_observable"

    # Check description for harness mismatch hints
    if desc:
        tags = [t.lower() for t in (desc.get("mechanism_tags") or [])]
        if any("harness_mismatch" in t or "wrong_binary" in t for t in tags):
            return "wrong_harness"

    # Default: assume trigger condition is unmet (most common for no-crash)
    return "trigger_condition_unmet"


def update_no_trigger_diagnosis(
    *,
    objective: dict[str, Any],
    failed_gate: str,
    miss_count: int,
) -> dict[str, Any]:
    """Update the no_trigger_diagnosis based on actual submit feedback.

    This is called by feedback arbitration after each no-trigger submit.

    Returns the updated objective dict (mutated in-place and returned).
    """
    oracle_kind = objective.get("oracle_kind", "")
    old_diag = objective.get("no_trigger_diagnosis", "unknown")

    # Rule 1: MSan + repeated no-trigger → oracle might not be observable
    if oracle_kind == "msan" and miss_count >= 2:
        objective["no_trigger_diagnosis"] = "oracle_not_observable"
        return objective

    # Rule 2: Semantic/parser oracle — never expect crash
    if oracle_kind in ("semantic_accept", "parser_reach"):
        objective["no_trigger_diagnosis"] = "oracle_not_observable"
        return objective

    # Rule 3: carrier_parse failure → likely wrong harness or format
    if failed_gate == "carrier_parse":
        objective["no_trigger_diagnosis"] = "wrong_harness"
        return objective

    # Rule 4: repeated no_crash_unknown → escalate diagnosis
    if failed_gate == "no_crash_unknown" and miss_count >= 2:
        # If the objective is lifetime and ASan can't observe, escalate
        if objective.get("kind") == "lifetime" and oracle_kind == "asan":
            objective["no_trigger_diagnosis"] = "oracle_not_observable"
        # wrong_harness is a strong signal; don't override it
        elif old_diag != "wrong_harness":
            # Could be either path not reached or trigger unmet
            objective["no_trigger_diagnosis"] = "path_not_reached"
        return objective

    # Rule 5: trigger_wrong_* → path reached, trigger condition unmet
    if failed_gate in ("trigger_wrong_signature", "trigger_wrong_location", "wrong_trigger"):
        objective["no_trigger_diagnosis"] = "trigger_condition_unmet"
        return objective

    # Default: keep existing diagnosis or set to trigger_condition_unmet
    if old_diag == "unknown":
        objective["no_trigger_diagnosis"] = "trigger_condition_unmet"
    return objective
