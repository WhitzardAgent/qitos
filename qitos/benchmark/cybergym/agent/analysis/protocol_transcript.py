"""Protocol transcript candidate builder — generates ProtocolTranscriptPlan
candidates from harness consumption patterns, function names, and event pairs.

Does NOT execute any protocol; only produces structured plans.
"""

from __future__ import annotations

import hashlib
from typing import Any


def build_transcript_candidates(
    *,
    ranked_paths: list[dict[str, Any]],
    mechanism_graphs: list[dict[str, Any]],
    harness: dict[str, Any] | None = None,
    crash_type: str = "",
    description_analysis: dict[str, Any] | None = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Build protocol transcript plan candidates for paths that require
    ordered multi-step input.

    Only generates candidates when evidence suggests a protocol/transcript
    is needed; does not force one on every path.
    """
    results: list[dict[str, Any]] = []

    for path in ranked_paths[:top_k]:
        path_id = str(path.get("path_id") or "")
        if not path_id:
            continue
        plan = _maybe_build_plan(
            path=path,
            mechanism_graphs=mechanism_graphs,
            harness=harness,
            crash_type=crash_type,
            description_analysis=description_analysis,
        )
        if plan:
            results.append(plan)

    return results


def _stable_transcript_id(objective_id: str, idx: int) -> str:
    material = f"{objective_id}|{idx}"
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"tr_{h}"


def _maybe_build_plan(
    *,
    path: dict[str, Any],
    mechanism_graphs: list[dict[str, Any]],
    harness: dict[str, Any] | None,
    crash_type: str,
    description_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a transcript plan if evidence suggests one is needed."""
    path_id = str(path.get("path_id") or "")
    event_pair = path.get("event_pair") or {}
    endpoint = path.get("endpoint") or {}
    endpoint_func = str(endpoint.get("function", "")).lower()

    # Determine if a transcript is needed
    needs_transcript = False
    scope = "file"
    steps: list[dict[str, Any]] = []

    # Check harness consumption pattern
    harness_pattern = ""
    if harness:
        consumption = harness.get("consumption") or {}
        harness_pattern = str(consumption.get("pattern", "") or "").lower()
        patterns = [p.lower() for p in (consumption.get("patterns") or [])]

        if any(k in harness_pattern for k in ("socket", "callback", "apdu", "multi_stage")):
            needs_transcript = True
            scope = harness_pattern
        for p in patterns:
            if any(k in p for k in ("socket", "callback", "apdu", "multi_stage")):
                needs_transcript = True
                scope = p

    # Check event pair for lifecycle patterns
    first_role = str(event_pair.get("first_role", "")).lower()
    second_role = str(event_pair.get("second_role", "")).lower()
    if first_role in ("invalidation", "free", "reset") and second_role in ("use", "access"):
        needs_transcript = True
        if not scope or scope == "file":
            scope = "multi_stage"

    # Check function/file names for protocol keywords
    protocol_keywords = {
        "frame", "packet", "handshake", "callback", "tasklet",
        "apdu", "session", "stream", "flush", "connect", "close",
        "init", "negotiate", "send", "recv", "dispatch",
    }
    if any(kw in endpoint_func for kw in protocol_keywords):
        needs_transcript = True

    # Check description for protocol hints
    if description_analysis:
        state_transitions = description_analysis.get("described_state_transitions") or []
        if state_transitions:
            needs_transcript = True
        mechanism_tags = [t.lower() for t in (description_analysis.get("mechanism_tags") or [])]
        if any("state" in t or "protocol" in t or "callback" in t for t in mechanism_tags):
            needs_transcript = True

    if not needs_transcript:
        return None

    # Build steps based on detected pattern
    if scope in ("socket", "callback", "apdu", "multi_stage"):
        steps = [
            {"step_id": "s0", "role": "init", "carrier": "bytes", "payload_hint": "create session/state"},
            {"step_id": "s1", "role": "send_frame", "carrier": "bytes", "payload_hint": f"valid header selects {endpoint_func}"},
            {"step_id": "s2", "role": "flush", "carrier": "bytes", "payload_hint": "force queued callback/tasklet"},
        ]
    elif first_role in ("invalidation", "free", "reset"):
        steps = [
            {"step_id": "s0", "role": "init", "carrier": "bytes", "payload_hint": "trigger state creation"},
            {"step_id": "s1", "role": "send_frame", "carrier": "bytes", "payload_hint": "trigger free/invalidation"},
            {"step_id": "s2", "role": "flush", "carrier": "bytes", "payload_hint": "access after invalidation"},
        ]
    else:
        steps = [
            {"step_id": "s0", "role": "init", "carrier": "bytes", "payload_hint": "setup initial state"},
            {"step_id": "s1", "role": "send_frame", "carrier": "bytes", "payload_hint": "trigger vulnerable path"},
        ]

    required_order = [s["step_id"] for s in steps]

    # Find matching objective
    graph_by_path = {g.get("ranked_path_id", ""): g for g in mechanism_graphs}
    graph = graph_by_path.get(path_id)
    obj_ids = (graph or {}).get("objective_ids", [])
    obj_id = obj_ids[0] if obj_ids else ""

    transcript_id = _stable_transcript_id(path_id, 0)

    return {
        "transcript_id": transcript_id,
        "ranked_path_id": path_id,
        "objective_id": obj_id,
        "steps": steps,
        "required_order": required_order,
        "harness_endpoint_scope": scope,
        "status": "candidate",
        "missing_evidence": [
            "confirm harness input contract",
            "verify step order from harness source",
        ],
    }
