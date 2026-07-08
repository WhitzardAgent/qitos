"""Mechanism graph builder — converts risk signals and ranked paths into
CrashMechanismGraph structures.

First version: pattern-based builder for heap/global OOB, stack underflow,
MSan origin/use, UAF/double-free. Conservative and explainable.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def build_mechanism_graphs(
    *,
    ranked_paths: list[dict[str, Any]],
    risk_signals_by_path: dict[str, list[dict[str, Any]]] | None = None,
    crash_type: str = "",
    description_analysis: dict[str, Any] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Build CrashMechanismGraph dicts from ranked paths and risk signals.

    Returns at most top_k graphs.
    """
    results: list[dict[str, Any]] = []
    signals_map = risk_signals_by_path or {}

    for path in ranked_paths[:top_k]:
        path_id = str(path.get("path_id") or "")
        if not path_id:
            continue
        graph = _build_single_graph(
            path=path,
            risk_signals=signals_map.get(path_id, []),
            crash_type=crash_type,
            description_analysis=description_analysis,
        )
        if graph:
            results.append(graph)

    return results


def _stable_graph_id(path_id: str, family: str) -> str:
    material = f"{path_id}|{family}"
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"mg_{h}"


def _build_single_graph(
    *,
    path: dict[str, Any],
    risk_signals: list[dict[str, Any]],
    crash_type: str,
    description_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a single mechanism graph from one ranked path."""
    path_id = str(path.get("path_id") or "")
    family = str(path.get("candidate_family") or "unknown")
    endpoint = path.get("endpoint") or {}
    endpoint_func = str(endpoint.get("function") or "")
    endpoint_file = str(endpoint.get("file") or "")
    endpoint_line = int(endpoint.get("line") or 0)
    event_pair = path.get("event_pair") or {}

    graph_id = _stable_graph_id(path_id, family)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    required_roles: list[str] = []
    missing_roles: list[str] = []
    objective_ids: list[str] = []

    # Determine mechanism family from crash_type and event_pair
    mech_family = _classify_mechanism_family(crash_type, event_pair, description_analysis)

    # Build nodes from the event pair
    if mech_family in ("heap_oob", "global_oob", "stack_oob"):
        _build_oob_graph(
            path=path,
            endpoint=endpoint,
            event_pair=event_pair,
            risk_signals=risk_signals,
            nodes=nodes,
            edges=edges,
            required_roles=required_roles,
            missing_roles=missing_roles,
        )
    elif mech_family == "origin_use":
        _build_origin_use_graph(
            path=path,
            endpoint=endpoint,
            event_pair=event_pair,
            risk_signals=risk_signals,
            nodes=nodes,
            edges=edges,
            required_roles=required_roles,
            missing_roles=missing_roles,
        )
    elif mech_family in ("use_after_free", "double_free"):
        _build_uaf_graph(
            path=path,
            endpoint=endpoint,
            event_pair=event_pair,
            risk_signals=risk_signals,
            nodes=nodes,
            edges=edges,
            required_roles=required_roles,
            missing_roles=missing_roles,
        )
    else:
        # Generic: just endpoint as access node
        nodes.append({
            "node_id": f"{graph_id}_access",
            "role": "access",
            "symbol_id": endpoint.get("symbol_id", ""),
            "function": endpoint_func,
            "file": endpoint_file,
            "line": endpoint_line,
            "expression": str(endpoint.get("signal", {}).get("expression", "")),
            "confidence": float(path.get("score", 0.5)),
        })
        required_roles.append("access")

    # Compute missing roles
    present_roles = {n.get("role") for n in nodes}
    for role in required_roles:
        if role not in present_roles:
            missing_roles.append(role)

    # Build summary
    chain_parts = []
    for n in nodes[:5]:
        role = n.get("role", "?")
        func = n.get("function", "")
        chain_parts.append(f"{role}:{func}" if func else role)
    summary = " -> ".join(chain_parts) if chain_parts else "unknown mechanism"
    if missing_roles:
        summary += f"; missing: {', '.join(missing_roles[:3])}"

    return {
        "graph_id": graph_id,
        "ranked_path_id": path_id,
        "mechanism_family": mech_family,
        "nodes": nodes,
        "edges": edges,
        "required_roles": required_roles,
        "missing_roles": missing_roles,
        "objective_ids": objective_ids,
        "summary": summary,
        "confidence": float(path.get("score", 0.5)),
    }


def _classify_mechanism_family(
    crash_type: str,
    event_pair: dict[str, Any],
    description_analysis: dict[str, Any] | None,
) -> str:
    """Classify the mechanism family from crash type and event pair."""
    ct = (crash_type or "").lower()
    ep = event_pair or {}

    if any(k in ct for k in ("heap-buffer-overflow", "heap-oob", "global-buffer")):
        return "heap_oob"
    if "stack-buffer" in ct or "stack-overflow" in ct:
        return "stack_oob"
    if any(k in ct for k in ("use-after-free", "use_of_uninitialized", "use-of-uninitialized")):
        # Distinguish MSan origin/use from UAF
        if "uninitial" in ct:
            return "origin_use"
        return "use_after_free"
    if "double-free" in ct:
        return "double_free"
    if "heap-use-after-free" in ct:
        return "use_after_free"

    # Fall back to event pair roles
    first_role = str(ep.get("first_role", "")).lower()
    second_role = str(ep.get("second_role", "")).lower()
    if "allocation" in first_role and "access" in second_role:
        return "heap_oob"
    if "invalidation" in first_role and "use" in second_role:
        return "use_after_free"
    if "origin" in first_role and "use" in second_role:
        return "origin_use"
    if "free" in first_role and ("use" in second_role or "access" in second_role):
        return "use_after_free"

    # Check description analysis for mechanism tags
    if description_analysis:
        tags = [t.lower() for t in (description_analysis.get("mechanism_tags") or [])]
        if any("oob" in t or "overflow" in t for t in tags):
            return "heap_oob"
        if any("uninitial" in t or "msan" in t for t in tags):
            return "origin_use"
        if any("use_after_free" in t or "uaf" in t or "double_free" in t for t in tags):
            return "use_after_free"

    return "unknown"


def _build_oob_graph(
    *,
    path: dict[str, Any],
    endpoint: dict[str, Any],
    event_pair: dict[str, Any],
    risk_signals: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    required_roles: list[str],
    missing_roles: list[str],
) -> None:
    """Build allocation -> guard -> access pattern for OOB."""
    path_id = str(path.get("path_id") or "")
    graph_prefix = f"mg_{path_id[:8]}"

    # Allocation node from event pair first role
    first = event_pair.get("first") or {}
    if first:
        nodes.append({
            "node_id": f"{graph_prefix}_alloc",
            "role": "allocation",
            "function": str(first.get("function", "")),
            "file": str(first.get("file", "")),
            "line": int(first.get("line", 0)),
            "expression": str(first.get("expression", "")),
            "memory_object": str(first.get("memory_object", "")),
            "size_expr": str(first.get("size_expr", "")),
            "confidence": 0.6,
        })
    required_roles.append("allocation")

    # Access node from endpoint
    nodes.append({
        "node_id": f"{graph_prefix}_access",
        "role": "access",
        "function": str(endpoint.get("function", "")),
        "file": str(endpoint.get("file", "")),
        "line": int(endpoint.get("line", 0)),
        "expression": str(endpoint.get("signal", {}).get("expression", "")),
        "memory_object": "",
        "index_expr": "",
        "confidence": float(path.get("score", 0.5)),
    })
    required_roles.append("access")

    # Check for guard from risk signals
    has_guard = False
    for sig in risk_signals:
        if sig.get("kind") in ("bounds_check", "size_guard", "range_check"):
            has_guard = True
            nodes.append({
                "node_id": f"{graph_prefix}_guard",
                "role": "guard",
                "function": str(sig.get("function", "")),
                "file": str(sig.get("file", "")),
                "line": int(sig.get("line", 0)),
                "expression": str(sig.get("expression", "")),
                "guard_expr": str(sig.get("expression", "")),
                "confidence": 0.7,
            })
            edges.append({
                "edge_id": f"{graph_prefix}_e_alloc_guard",
                "src": f"{graph_prefix}_alloc",
                "dst": f"{graph_prefix}_guard",
                "relation": "bounds",
                "confidence": 0.5,
            })
            edges.append({
                "edge_id": f"{graph_prefix}_e_guard_access",
                "src": f"{graph_prefix}_guard",
                "dst": f"{graph_prefix}_access",
                "relation": "dataflow",
                "confidence": 0.5,
            })
            break
    required_roles.append("guard")
    if not has_guard:
        missing_roles.append("guard")

    # If no allocation from event pair, add direct edge
    if first:
        if not has_guard:
            edges.append({
                "edge_id": f"{graph_prefix}_e_alloc_access",
                "src": f"{graph_prefix}_alloc",
                "dst": f"{graph_prefix}_access",
                "relation": "bounds",
                "confidence": 0.5,
            })


def _build_origin_use_graph(
    *,
    path: dict[str, Any],
    endpoint: dict[str, Any],
    event_pair: dict[str, Any],
    risk_signals: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    required_roles: list[str],
    missing_roles: list[str],
) -> None:
    """Build origin -> use pattern for MSan uninitialized."""
    path_id = str(path.get("path_id") or "")
    graph_prefix = f"mg_{path_id[:8]}"

    # Origin node
    first = event_pair.get("first") or {}
    if first:
        nodes.append({
            "node_id": f"{graph_prefix}_origin",
            "role": "sanitizer_origin",
            "function": str(first.get("function", "")),
            "file": str(first.get("file", "")),
            "line": int(first.get("line", 0)),
            "expression": str(first.get("expression", "")),
            "confidence": 0.6,
        })
    required_roles.append("sanitizer_origin")

    # Use node
    nodes.append({
        "node_id": f"{graph_prefix}_use",
        "role": "access",
        "function": str(endpoint.get("function", "")),
        "file": str(endpoint.get("file", "")),
        "line": int(endpoint.get("line", 0)),
        "expression": str(endpoint.get("signal", {}).get("expression", "")),
        "confidence": float(path.get("score", 0.5)),
    })
    required_roles.append("access")

    edges.append({
        "edge_id": f"{graph_prefix}_e_origin_use",
        "src": f"{graph_prefix}_origin" if first else "",
        "dst": f"{graph_prefix}_use",
        "relation": "dataflow",
        "condition": "field left uninitialized",
        "confidence": 0.6,
    })

    if not first:
        missing_roles.append("sanitizer_origin")


def _build_uaf_graph(
    *,
    path: dict[str, Any],
    endpoint: dict[str, Any],
    event_pair: dict[str, Any],
    risk_signals: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    required_roles: list[str],
    missing_roles: list[str],
) -> None:
    """Build free -> reuse pattern for UAF / double-free."""
    path_id = str(path.get("path_id") or "")
    graph_prefix = f"mg_{path_id[:8]}"

    # Free / invalidation node
    first = event_pair.get("first") or {}
    if first:
        nodes.append({
            "node_id": f"{graph_prefix}_free",
            "role": "free",
            "function": str(first.get("function", "")),
            "file": str(first.get("file", "")),
            "line": int(first.get("line", 0)),
            "expression": str(first.get("expression", "")),
            "confidence": 0.6,
        })
    required_roles.append("free")

    # Access / reuse node
    nodes.append({
        "node_id": f"{graph_prefix}_access",
        "role": "access",
        "function": str(endpoint.get("function", "")),
        "file": str(endpoint.get("file", "")),
        "line": int(endpoint.get("line", 0)),
        "expression": str(endpoint.get("signal", {}).get("expression", "")),
        "confidence": float(path.get("score", 0.5)),
    })
    required_roles.append("access")

    edges.append({
        "edge_id": f"{graph_prefix}_e_free_access",
        "src": f"{graph_prefix}_free" if first else "",
        "dst": f"{graph_prefix}_access",
        "relation": "temporal_order",
        "condition": "access after free/invalidation",
        "confidence": 0.6,
    })

    if not first:
        missing_roles.append("free")
