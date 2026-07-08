"""Heuristic reachability frontier classification from existing evidence."""

from __future__ import annotations

from typing import Any


def classify_reachability_frontier(
    *,
    submit_result: dict[str, Any] | None = None,
    consistency_signals: list[dict[str, Any]] | None = None,
    harness_protocols: list[dict[str, Any]] | None = None,
    latest_sanity: dict[str, Any] | None = None,
    objectives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    submit_result = submit_result or {}
    consistency_signals = consistency_signals or []
    harness_protocols = harness_protocols or []
    latest_sanity = latest_sanity or {}
    objectives = objectives or []
    text = " ".join(str(v) for v in submit_result.values()).lower()

    if latest_sanity and latest_sanity.get("passed") is False:
        return _probe("path_not_reached", "harness_accept", "repair_carrier", "carrier sanity failed before meaningful trigger")

    for signal in consistency_signals:
        if signal.get("blocks_submit") or signal.get("severity") == "block":
            action = "extract_harness_protocol" if "scope" in str(signal.get("kind", "")).lower() else "repair_consistency"
            return _probe("path_not_reached", "harness_accept", action, str(signal.get("summary") or "consistency block"))

    if "wrong harness" in text or "binary mismatch" in text:
        return _probe("wrong_harness", "harness_accept", "extract_harness_protocol", "feedback indicates harness/binary mismatch")

    if "timeout" in text:
        return _probe("frontier_reached", "pre_sink", "localize_field", "timeout likely reached parser/loop frontier")

    if "no crash" in text or "not trigger" in text or "no_trigger" in text:
        for obj in objectives:
            if obj.get("observable_by_submit") is False or obj.get("no_trigger_diagnosis") == "oracle_not_observable":
                return _probe("oracle_not_observable", "sink", "verify_oracle_context", "objective oracle is not observable by submit")
        if harness_protocols:
            return _probe("trigger_unmet", "dispatch", "localize_field", "harness accepted input but trigger condition was not met")
        return _probe("path_not_reached", "parser_accept", "repair_carrier", "no-trigger without protocol acceptance evidence")

    if "asan" in text or "ubsan" in text or "msan" in text or "crash" in text:
        return _probe("frontier_reached", "sink", "verify_oracle_context", "sanitizer/crash signal observed")

    return _probe("unknown", "unknown", "extract_harness_protocol", "insufficient feedback to classify frontier")


def _probe(status: str, frontier: str, action: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "frontier": frontier,
        "recommended_action": action,
        "reason": reason,
    }
