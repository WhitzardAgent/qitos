"""Oracle classification for CyberGym submit feedback planning."""

from __future__ import annotations

from typing import Any


def classify_oracle(
    *,
    crash_type: str,
    vulnerability_description: str = "",
    mechanism_graph: dict[str, Any] | None = None,
    ranked_path: dict[str, Any] | None = None,
    harness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = " ".join(
        str(item or "")
        for item in (
            crash_type,
            vulnerability_description,
            mechanism_graph,
            ranked_path,
            harness,
        )
    ).lower()

    oracle_kind = "asan"
    signal = "sanitizer_crash"
    observable = True
    reason = "ASan-style crash is normally observable by submit_poc"
    confidence = 0.65
    action = "continue"

    if "msan" in text or "uninitial" in text:
        oracle_kind = "msan"
        signal = "msan_use_of_uninitialized_value"
        confidence = 0.78
        if "msan" not in str(harness or {}).lower() and "msan" not in str(crash_type or "").lower():
            observable = False
            reason = "MSan objective may require an MSan-instrumented harness/binary"
            action = "verify_harness"
        else:
            reason = "MSan signal appears aligned with crash description/harness"
    elif "ubsan" in text or "undefined" in text:
        oracle_kind = "ubsan"
        signal = "undefined_behavior"
        reason = "UBSan-style report is expected"
        confidence = 0.72
    elif "leak" in text or "lsan" in text:
        oracle_kind = "leak"
        signal = "leak_report"
        confidence = 0.68
    elif "assert" in text:
        oracle_kind = "assert"
        signal = "assertion_failure"
        confidence = 0.7
    elif "semantic" in text or "accept" in text:
        oracle_kind = "semantic_accept"
        signal = "semantic_acceptance"
        observable = False
        reason = "Semantic acceptance is not necessarily a crash oracle"
        action = "switch_objective"
        confidence = 0.62
    elif "parser reach" in text or "parser_reach" in text:
        oracle_kind = "parser_reach"
        signal = "parser_reachability"
        observable = False
        reason = "Parser reachability needs a downstream crash/observable objective"
        action = "switch_objective"
        confidence = 0.6
    elif not text.strip():
        oracle_kind = "unknown"
        signal = "unknown"
        observable = True
        reason = "Insufficient oracle evidence; submit_poc remains authoritative"
        confidence = 0.2

    return {
        "oracle_kind": oracle_kind,
        "oracle_signal": signal,
        "observable_by_submit": observable,
        "observability_reason": reason,
        "confidence": confidence,
        "recommended_action": action,
    }
