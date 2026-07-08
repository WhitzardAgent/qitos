"""Task-persistent memory management — extracted from agent.py."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from qitos.core.memory import MemoryRecord

from ...state import CyberGymState


def update_task_persistent_memory(
    agent: Any, state: CyberGymState, old_phase: str, new_phase: str
) -> None:
    """Update the four task-persistent memory fields that survive compaction.

    Called once per step in reduce() after phase advancement.
    """
    # 1. Vulnerability analysis — updated when entering formulation
    #    or when trigger_hypothesis/crash details change.
    if new_phase == "formulation" and old_phase != "formulation":
        parts = []
        if state.bug_type:
            parts.append(f"Bug type: {state.bug_type}")
        if state.vulnerable_functions:
            parts.append(f"Sink: {', '.join(state.vulnerable_functions[:3])}")
        if state.trigger_hypothesis:
            parts.append(state.trigger_hypothesis)
        # Include confirmed gate conditions
        confirmed = state.confirmed_gates() if hasattr(state, "confirmed_gates") else []
        for g in confirmed[:4]:
            parts.append(f"[gate] {g.required_condition}")
        if parts:
            analysis = ". ".join(parts)
            state.vulnerability_analysis = analysis

    # 2. Path trace — updated from chain nodes
    nodes = list(getattr(state, "call_chain_nodes", []) or [])
    if nodes:
        sorted_nodes = sorted(nodes, key=lambda n: n.order)
        trace = []
        for n in sorted_nodes[:8]:
            loc = n.location.split(":")[0] if ":" in n.location else n.location
            trace.append(f"{n.function} ({loc})")
        state.path_trace = trace

    # 3. Attempt history compact — append after each submit
    if state.last_verification_result and state.last_submitted_poc_path:
        poc_path = state.last_submitted_poc_path
        vul_exit = state.last_verification_result.get("vul_exit_code")
        fix_exit = state.last_verification_result.get("fix_exit_code")
        accepted = state.last_verification_result.get("accepted") is True
        gate = agent._classify_failed_gate(state.last_verification_result)
        scope = str(state.last_verification_result.get("verification_scope") or "")
        if accepted:
            outcome = "SUCCESS"
        elif vul_exit and vul_exit != 0:
            outcome = f"vul_crash({vul_exit})"
        else:
            outcome = "no_crash_unknown"
        # Versioned archive name (matches .cybergym/poc_archive/ files)
        version = state.poc_attempts
        suffix = Path(poc_path).suffix  # preserve original: .pcap, .png, .b2frame, etc.
        archived_name = f"poc_v{version}{suffix}"
        # Build structured failure analysis
        parts = [f"#{version} {archived_name}: {outcome}"]
        if gate:
            parts.append(f"[{gate}]")
        # Add crash details if available
        crash_info = []
        if state.crash_type:
            crash_info.append(state.crash_type)
        if state.crash_location:
            crash_info.append(f"@ {state.crash_location}")
        if crash_info and outcome != "SUCCESS":
            parts.append(f"crash={', '.join(crash_info)}")
        # Add discriminant info if available
        if fix_exit is not None and fix_exit != 0 and scope == "full":
            parts.append("fix_also_crashed")
        elif vul_exit and vul_exit != 0 and scope == "vul_only":
            parts.append("precision_unverified")
        # Add action hint (one-line from gate type)
        action_hint = attempt_action_hint(gate)
        if action_hint:
            parts.append(action_hint)
        entry = " ".join(parts)
        # Deduplicate by version number (#N at start of entry)
        existing_versions = set()
        for e in state.attempt_history_compact:
            m = re.match(r'#(\d+)', e)
            if m:
                existing_versions.add(m.group(0))
        if f"#{version}" not in existing_versions:
            state.attempt_history_compact.append(entry)
        state.attempt_history_compact = state.attempt_history_compact[-10:]

    # 4. Current hypothesis — updated after every non-accepted submit
    if state.last_verification_result and not state.is_verified():
        gate = agent._classify_failed_gate(state.last_verification_result)
        vul_exit = state.last_verification_result.get("vul_exit_code")
        ct = state.crash_type or ""
        cl = state.crash_location or ""
        hypothesis_map = {
            "path_not_reached": hypothesis_path_not_reached(state),
            "no_crash_unknown": (
                "No crash observed. This does not prove the vulnerability path was missed; "
                "it may also mean the path was reached but the sink guard/value/size/state "
                "was not satisfied. Decide between reachability and trigger-condition failure "
                "using focused source evidence and PoC byte inspection before submitting again."
            ),
            "carrier_parse": (
                "Input format rejected at harness entry — fix carrier format. "
                "Check magic bytes, header structure, and minimum size. "
                "Use `file` and `xxd` on existing PoC to diagnose."
            ),
            "malformed_substructure": (
                f"Input parsed but sub-structure invalid — fix field layout. "
                f"Check struct sizes, alignment, and field offsets against source."
                + (f" Crash: {ct} at {cl}" if ct else "")
            ),
            "trigger_wrong_signature": (
                f"ASAN detected corruption but wrong crash type. "
                f"Crash: {ct} at {cl}. "
                "Refine overflow parameters (size/offset/field values)."
            ),
            "trigger_wrong_location": (
                f"Crash in wrong location: {cl}. "
                "The overflow hits an unexpected code path — adjust the target "
                "field/offset to hit the vulnerable function specifically."
            ),
            "wrong_trigger": (
                "PoC crashes but trigger condition is wrong. "
                "Read the comparison/guard in the vulnerable function to find "
                "the exact trigger value needed."
            ),
            "timeout_not_crash": (
                "PoC causes timeout but no crash — execution is stuck. "
                "Simplify: reduce nesting/depth, aim for shortest path to vulnerability."
            ),
            "discriminant_failed": (
                f"Both vul and fix binaries crash — PoC is too aggressive. "
                f"Crash: {ct} at {cl}. "
                "Reduce overflow to MINIMAL (1-4 bytes past boundary). "
                "The fix must distinguish the overflow; if both crash, it's not precise."
            ),
            "vul_only_triggered": (
                f"VUL-ONLY TRIGGER: binary crashed (exit={vul_exit}). "
                + (f"Crash: {ct} at {cl}. " if ct else "")
                + "PARTIAL success — refine for precision. "
                "Reduce overflow to minimal bytes, target exact offset, study patch diff."
            ),
            "duplicate_candidate": (
                "Same PoC content already submitted — change the PoC before resubmitting."
            ),
        }
        new_hypothesis = hypothesis_map.get(gate)
        if new_hypothesis:
            state.current_hypothesis = new_hypothesis


def hypothesis_path_not_reached(state: CyberGymState) -> str:
    """Generate hypothesis text for path_not_reached gate."""
    first_open = state.first_open_gate() if hasattr(state, "first_open_gate") else None
    if first_open:
        return (
            f"Path not reached — first open gate: {first_open.description}. "
            f"Need to confirm: {first_open.required_condition}"
        )
    return (
        "Path not reached — identify and confirm the parser gate "
        "that blocks input from reaching the vulnerable code."
    )


def attempt_action_hint(gate: str) -> str:
    """Return a one-line action hint for the attempt history entry."""
    hints = {
        "carrier_parse": "→ fix magic bytes/headers",
        "path_not_reached": "→ route input to vulnerable function",
        "no_crash_unknown": "→ classify miss: reachability vs trigger",
        "malformed_substructure": "→ fix field sizes/offsets",
        "trigger_wrong_signature": "→ adjust overflow size/offset",
        "trigger_wrong_location": "→ target exact vulnerable field",
        "wrong_trigger": "→ match exact trigger value",
        "timeout_not_crash": "→ simplify PoC",
        "discriminant_failed": "→ reduce overflow to minimal",
        "vul_only_triggered": "→ refine for precision",
        "duplicate_candidate": "→ change PoC content",
    }
    return hints.get(gate, "")


def save_success_memory(agent: Any, state: CyberGymState) -> None:
    """Save a feedback-type memory after successful PoC generation."""
    if not agent.memory:
        return

    bug_type = state.bug_type or "unknown"
    name = f"{bug_type}_poc_strategy"
    description = f"Proven strategy for {bug_type} input PoCs"

    content_parts = [
        f"Successfully generated PoC for task {state.task_id}",
        f"Bug type: {bug_type}",
        f"Affected component: {state.affected_component}",
    ]
    if state.vulnerable_functions:
        content_parts.append(f"Vulnerable functions: {', '.join(state.vulnerable_functions[:5])}")
    if state.trigger_hypothesis:
        content_parts.append(f"Trigger hypothesis: {state.trigger_hypothesis}")
    content_parts.append(f"Attempts needed: {state.poc_attempts}")

    content = "\n".join(content_parts)

    agent.memory.append(
        MemoryRecord(
            role="feedback",
            content=content,
            step_id=state.current_step,
            metadata={
                "type": "feedback",
                "name": name,
                "description": description[:150],
            },
        )
    )
