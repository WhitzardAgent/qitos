"""Sink candidate management — extracted from agent.py."""
from __future__ import annotations

import hashlib
import re as _re
from typing import Any

from ...state import CyberGymState, SinkCandidate


def advance_sink_candidate(agent: Any, state: CyberGymState) -> bool:
    """After failure on current sink, try the next candidate. Returns True if rotated."""
    current = state.active_sink_id
    active = sorted(
        [c for c in state.sink_candidates if c.status != "eliminated"],
        key=lambda c: -c.confidence,
    )
    if len(active) <= 1:
        return False
    for i, c in enumerate(active):
        sid = f"{c.function}@{c.location}"
        if sid == current:
            if i + 1 < len(active):
                # Mark current as eliminated and rotate
                c.status = "eliminated"
                c.evidence = (c.evidence + " [eliminated: repeated PoC failures]") if c.evidence else "Eliminated: repeated PoC failures"
                next_sink = active[i + 1]
                state.active_sink_id = f"{next_sink.function}@{next_sink.location}"
                return True
            return False
    # No current match; set to best
    best = active[0]
    state.active_sink_id = f"{best.function}@{best.location}"
    return True


def auto_resolve_harness_on_read(agent: Any, state: CyberGymState, read_output: str) -> None:
    """Auto-resolve harness when agent READs a harness candidate file.

    If the READ output contains content from a harness candidate path
    (e.g., patch_parse_fuzzer.c), mark the harness as confirmed.
    """
    harness_candidates = list(getattr(state, "harness_candidates", []) or [])
    if not harness_candidates:
        return
    for hc in harness_candidates:
        if hc.source_path and hc.source_path in read_output:
            state.harness_entry_confirmed = True
            if hasattr(state, "input_format") and hasattr(state.input_format, "confirmed"):
                state.input_format.confirmed = True
            state.metadata["harness_entry_confirmed"] = True
            # Also update the harness resolution if available
            resolution = getattr(state, "harness_resolution", None)
            if resolution and hasattr(resolution, "status"):
                if resolution.status == "unresolved":
                    resolution.status = "confirmed"
            break


def auto_promote_sink(agent: Any, state: CyberGymState) -> None:
    """Force-promote the best available candidate to a confirmed sink.

    Called when step >= 4 and no confirmed sink exists yet. This guarantees
    the formulation phase always has a target, even when the LLM never
    called record_sink_candidate explicitly.
    """
    from ...analysis.vuln_patterns import is_entry_point_function

    # First try: promote a static_navigation candidate that isn't an entry point
    candidates = [
        c for c in state.sink_candidates
        if c.status != "eliminated"
        and not is_entry_point_function(c.function)
        and c.source in {"static_navigation", "graph_auto_deepen"}
    ]
    candidates.sort(key=lambda c: -c.confidence)

    if candidates:
        best = candidates[0]
        best.metadata = dict(best.metadata or {})
        best.metadata["original_source"] = best.source  # preserve provenance
        best.source = "model_candidate"
        best.status = "candidate"
        best.metadata["requires_review"] = False
        best.metadata["reviewed"] = True
        best.metadata["auto_promoted"] = True
        best.metadata["confirmed_via"] = "auto_promotion_step4"
        state.active_sink_id = state._primary_sink_id()
        state.active_sink_candidate_id = best.candidate_id
        state.analysis_status = "TARGET_PROPOSED"
        state.metadata["_pending_sink_analysis"] = best.candidate_id
        state.sink_hypothesis_source = "auto_promoted"
        return

    # Second try: promote a description-derived candidate (high confidence only)
    # Only promote candidates with confidence >= 0.5 to avoid promoting
    # noise words extracted by regex from the vulnerability description.
    desc_candidates = [
        c for c in state.sink_candidates
        if c.status != "eliminated"
        and not is_entry_point_function(c.function)
        and c.source in {"description", "description_symbol"}
        and c.confidence >= 0.5  # reject low-confidence noise
    ]
    desc_candidates.sort(key=lambda c: -c.confidence)

    if desc_candidates:
        best = desc_candidates[0]
        best.metadata = dict(best.metadata or {})
        best.metadata["original_source"] = best.source  # preserve provenance
        best.source = "model_candidate"
        best.status = "candidate"
        best.metadata["requires_review"] = False
        best.metadata["reviewed"] = True
        best.metadata["auto_promoted"] = True
        best.metadata["confirmed_via"] = "auto_promotion_desc"
        state.active_sink_id = state._primary_sink_id()
        state.active_sink_candidate_id = best.candidate_id
        state.analysis_status = "TARGET_PROPOSED"
        state.metadata["_pending_sink_analysis"] = best.candidate_id
        state.sink_hypothesis_source = "auto_promoted"


def suggest_sink_from_asan_feedback(agent: Any, state: CyberGymState, output: dict) -> None:
    """V12: After a PoC miss with ASAN output, suggest a new sink hypothesis
    based on the actual crash location if it differs from the current sink."""
    crash_type = str(getattr(state, "crash_type", "") or "")
    crash_location = str(getattr(state, "crash_location", "") or "")
    if not crash_type and not crash_location:
        return

    # Parse function from ASAN stack trace
    vul_stderr = str(output.get("vul_stderr", "") or "")
    raw_output = str(output.get("raw_output", "") or "")
    crash_source = vul_stderr if vul_stderr else raw_output

    crash_func = ""
    # Try to extract top frame from ASAN stack summary
    # Pattern: "#0 0x... in func_name file.c:line:col"
    m = _re.search(r'#0\s+0x[0-9a-f]+\s+in\s+([A-Za-z_]\w+)', crash_source)
    if m:
        crash_func = m.group(1)
    elif crash_location:
        # Fallback: try to parse from crash_location
        crash_func = crash_location.rsplit(":", 1)[0] if ":" in crash_location else ""

    if not crash_func:
        return

    # Skip if same as current active sink
    active_sinks = state.confirmed_sink_candidates()
    if active_sinks and active_sinks[0].function.lower() == crash_func.lower():
        return

    # Check if this function is already a candidate
    existing = next(
        (c for c in state.sink_candidates
         if c.function.lower() == crash_func.lower() and c.status != "eliminated"),
        None
    )
    if existing:
        existing.confidence = min(1.0, existing.confidence + 0.15)
        existing.evidence = (
            f"ASAN crash at this function (crash_type={crash_type}). "
            + (existing.evidence or "")
        )
    else:
        crash_file = crash_location.rsplit(":", 1)[0] if ":" in crash_location else ""
        crash_line = 0
        if ":" in crash_location:
            _parts = crash_location.rsplit(":", 1)
            if _parts[-1].isdigit():
                crash_line = int(_parts[-1])
        new_sink = SinkCandidate(
            function=crash_func,
            location=crash_location,
            confidence=0.6,
            evidence=f"ASAN crash at this function (crash_type={crash_type}). "
                     f"Actual crash differs from current sink hypothesis.",
            status="candidate",
            source="asan_feedback",
            file=crash_file,
            line=crash_line,
            reason=f"ASAN-reported crash location: {crash_type} at {crash_location}",
            metadata={
                "requires_review": False,
                "confirmed_via": "asan_feedback",
                "auto_promoted": False,
                "crash_type": crash_type,
            },
        )
        material = f"{new_sink.repository_id}|{new_sink.file}|{new_sink.line}|{new_sink.function}||"
        new_sink.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
        state.sink_candidates.append(new_sink)

    state.pending_reminder = (
        f"ASAN crash at `{crash_func}` ({crash_location}) differs from "
        f"your current sink target. Consider calling "
        f"`record_sink_candidate(\"{crash_func}\", ...)` to update your sink "
        f"hypothesis, or continue refining the PoC to reach your current target."
    )
    state.pending_reminder_signature = "asan-sink-hypothesis"
    state.sink_hypothesis_source = "asan_feedback"
