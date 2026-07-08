"""Gate refutation — mark chain gates as refuted/questioned after failed submits.

Extracted from FeedbackMixin to reduce mixin.py size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from ...state import CyberGymState

# Repair hints for specific gate classifications
FAILED_GATE_REPAIR_HINTS: Dict[str, str] = {
    "carrier_parse": (
        "The PoC file could not be parsed by the harness. "
        "Fix the carrier format — ensure valid headers, checksums, and container structure. "
        "Do NOT regenerate from scratch; fix the existing carrier."
    ),
    "path_not_reached": (
        "The input ran without crashing. The vul-side result alone can't tell whether the "
        "vulnerable path was NOT REACHED, or was reached but the TRIGGER condition "
        "(value/size/state) was not met. Check both: (a) is the target reachable from the "
        "harness entry, and (b) does your trigger field/size actually satisfy the bug "
        "condition — adjust whichever is wrong instead of assuming it is only a path problem."
    ),
    "no_crash_unknown": (
        "The vulnerable binary exited normally. This is only a no-crash observation: it does "
        "not prove the path was missed, and it does not prove the path was reached. Decide "
        "between reachability and trigger-condition failure using source evidence before "
        "changing the plan."
    ),
    "malformed_substructure": (
        "The carrier parsed but the target data structure is malformed. "
        "Adjust field sizes, table shapes, or block layouts within the carrier, "
        "keeping the outer container intact."
    ),
    # P37: split wrong_trigger into two distinct failure modes
    "trigger_wrong_signature": (
        "The PoC reached the vulnerable code and triggered memory corruption (ASAN detected), "
        "but the crash signature doesn't match the expected one. You're very close — refine "
        "the trigger parameters (overflow size, offset, field value) to match the expected "
        "crash type and location."
    ),
    "trigger_wrong_location": (
        "The PoC caused a crash but in an unexpected location — the input reached some code "
        "but not the vulnerable path. Reconsider the input routing: which path-gating "
        "conditions must be satisfied to direct execution toward the target function?"
    ),
    "wrong_trigger": (
        "The input reached the parser but did not satisfy the vulnerability condition. "
        "Change the trigger bytes, field values, or state transitions that lead to the bad state."
    ),
    "timeout_not_crash": (
        "Execution timed out without a crash. Reduce input complexity or aim for a "
        "shorter deterministic path to the vulnerability."
    ),
    "duplicate_candidate": (
        "This PoC was already submitted. Modify the content before resubmitting."
    ),
    "discriminant_failed": (
        "Reduce overflow magnitude to minimal (1-4 bytes). The fix's bounds check must "
        "be able to catch the overflow — if both binaries crash, the PoC is too aggressive."
    ),
    "vul_only_triggered": (
        "Vulnerability triggered but precision is UNVERIFIED — fix-side data is unavailable. "
        "Refine for PRECISION: reduce overflow magnitude to minimal (1-4 bytes past boundary), "
        "target the exact vulnerable field/offset, and ensure the fix's bounds check can still "
        "prevent the crash. Study the patch diff to understand what the fix checks. "
        "A PoC that crashes both binaries will be rejected — make overflow surgical."
    ),
}


def _poc_header_hex(state: CyberGymState) -> str:
    """Read first 16 bytes of last submitted PoC and return as hex string."""
    poc_path = getattr(state, "last_submitted_poc_path", "")
    if not poc_path:
        return ""
    workspace = str(state.workspace_root or "")
    import os as _os
    full_path = _os.path.join(workspace, poc_path) if workspace else poc_path
    try:
        with open(full_path, "rb") as f:
            header = f.read(16)
        return " ".join(f"{b:02X}" for b in header) if header else ""
    except (OSError, ValueError):
        return ""


def failed_gate_repair_hint(gate: str) -> str:
    """Return repair hint string for a given failed gate classification."""
    return FAILED_GATE_REPAIR_HINTS.get(gate, "")


def carrier_parse_repair_hint(fmt_type: str, magic: str, poc_hex: str = "") -> str:
    """Build a carrier-parse repair hint using real toolbox capabilities."""
    hex_info = f" Your PoC starts with: {poc_hex}." if poc_hex else ""
    fmt_label = (fmt_type or "").strip()
    fmt_info = f" ({fmt_label})" if fmt_label else ""
    base = f"Carrier format parse failed. Expected magic bytes: {magic}{fmt_info}.{hex_info}"
    if not fmt_label:
        return (
            f"{base} Fix the carrier header, magic bytes, checksums, and container "
            "structure before changing the trigger bytes."
        )

    try:
        from ...toolbox.capabilities import inspect_command, minimal_command, normalize_format, supports
    except Exception:
        return (
            f"{base} Fix the carrier header or use a task-local valid seed as the "
            "carrier, then inject the overflow into the target field."
        )

    fmt = normalize_format(fmt_label)
    if supports(fmt, "minimal"):
        build_cmd = minimal_command(fmt, "poc.bin")
        inspect_cmd = inspect_command(fmt, "poc.bin")
        return (
            f"{base} Fix the carrier header or use `{build_cmd}` to create a valid "
            f"{fmt} carrier, inject the overflow into the target field, then run "
            f"`{inspect_cmd}` before submit."
        )

    return (
        f"{base} No toolbox minimal builder is available for {fmt_label}; use a "
        "task-local corpus seed or known-good sample as the carrier, inspect bytes "
        "with hex_view/struct_probe, and mutate only the target field."
    )


def feedback_action_guidance(agent: Any, state: CyberGymState) -> str:
    """Return concrete tool/action guidance based on latest failed gate."""
    result = state.last_verification_result
    if not result or state.is_verified():
        return ""
    gate = agent._classify_failed_gate(dict(result))
    if not gate:
        return ""
    guidance_map = {
        "carrier_parse": (
            "Action: Check carrier format with `BASH` (e.g., `file poc.bin`, `xxd poc.bin | head`). "
            "Fix headers/checksums. Consider using a known-good sample as base."
        ),
        "path_not_reached": (
            "Action: No crash — this can be EITHER a reachability OR a trigger problem; "
            "do not assume it is only the path. (a) Reachability: check the target is reachable "
            "from the HARNESS ENTRY — some crash paths depend on runtime state (e.g., fuzzshark "
            "sets cinfo=NULL, short-circuiting col_append_str); if unreachable in the fuzzer, "
            "find an alternative path. (b) Trigger: if the path IS reached, the value/size/state "
            "at the vulnerable site does not yet satisfy the bug — adjust that field. READ the "
            "vulnerable function to decide which of (a)/(b) applies, then fix the corresponding PoC field."
        ),
        "no_crash_unknown": (
            "Action: No crash observed. Do NOT assume path_not_reached. First classify the miss: "
            "(a) path not reached from the harness, or (b) path likely reached but trigger bytes/size/state "
            "did not satisfy the sink guard. Use the current vulnerability path, focused read/grep, "
            "and PoC byte inspection to pick one concrete fix."
        ),
        "malformed_substructure": (
            "Action: read the vulnerable function to identify the exact struct layout expected. "
            "Compare with your current PoC's binary layout using `BASH` (hexdump). "
            "Adjust field sizes and offsets."
        ),
        # P37: specific guidance for the two new trigger-failure modes
        "trigger_wrong_signature": (
            "Action: You're close! ASAN detected memory corruption but the crash type doesn't match. "
            "Refine the overflow size, offset, or field values to trigger the exact vulnerability class "
            "described in the task. Small adjustments to trigger parameters often suffice."
        ),
        "trigger_wrong_location": (
            "Action: The PoC crashes in an unexpected location — the input path doesn't reach the "
            "vulnerable function. READ the path from harness entry to the target, identify which "
            "path-gating condition is routing execution away from the vulnerability, and fix that field."
        ),
        "wrong_trigger": (
            "Action: Focus on the trigger condition — what value/size/state must be different? "
            "Read the comparison/guard in the vulnerable function, then change the trigger bytes."
        ),
        "timeout_not_crash": (
            "Action: Simplify the PoC — reduce nesting, remove unnecessary layers. "
            "Aim for the shortest path from harness input to vulnerable function."
        ),
        "discriminant_failed": (
            "Action: Your overflow is too broad — the fix also crashes. "
            "Make the overflow PRECISE and MINIMAL: reduce overflow size to just "
            "1-4 bytes past the boundary, target the exact vulnerable field offset, "
            "and ensure the fix's bounds check can distinguish your PoC from a "
            "legitimate input. Smaller overflow = better discriminability."
        ),
        "vul_only_triggered": (
            "Action: PARTIAL HIT — vulnerability triggered but precision is unverified. "
            "Refine the PoC for maximal precision: reduce overflow to minimal bytes "
            "(1-4 past boundary), target the exact vulnerable field/offset from source "
            "code, and ensure only the vulnerable code path is exercised. Study the "
            "patch diff if available to understand what the fix checks. If both binaries "
            "crash, the PoC will be rejected — make the overflow surgical."
        ),
    }
    return guidance_map.get(gate, "")


def refute_matching_gates(state: CyberGymState, gate: str) -> None:
    """Refute ChainGate entries based on the failed gate classification.

    After a failed submit_poc, this marks relevant gates as 'refuted'
    and derives repair hints with diagnostic information instead of
    circular "READ the code" guidance.  Refuted gates are never deleted —
    they carry learning that prevents the agent from retrying the same approach.
    """
    if not gate or not hasattr(state, "call_chain_gates"):
        return
    if gate == "no_crash_unknown":
        # A normal vul-side exit is not enough evidence to refute or even
        # question a reachability gate. The path may have been missed, or
        # the path may have been reached with an unsatisfied trigger guard.
        return

    # Record pre-status for diagnostics emission at the end
    pre_status = {id(g): g.status for g in state.call_chain_gates}

    # Get gates that are still open (inferred/unknown/questioned) for refutation
    open_gates = [
        (i, g) for i, g in enumerate(state.call_chain_gates)
        if g.status in ("inferred", "unknown", "questioned")
    ]

    # Diagnostic helper: extract PoC header hex for repair hints
    poc_hex = _poc_header_hex(state)

    if gate == "carrier_parse":
        # Input couldn't be parsed at all — generate concrete repair hint
        # from InputFormatModel if available
        fmt = getattr(state, "input_format", None)
        magic = getattr(fmt, "magic_bytes", "") if fmt else ""
        fmt_type = getattr(fmt, "format_type", "") if fmt else ""
        for i, g in open_gates:
            if g.gate_type == "format_gate":
                g.status = "refuted"
                if magic:
                    g.repair_hint = carrier_parse_repair_hint(fmt_type, magic, poc_hex)
                else:
                    g.repair_hint = (
                        "Input failed to parse at harness entry — fix carrier format. "
                        "Check magic bytes, header structure, and container validity."
                    )
                g.evidence = "Refuted by carrier_parse failure"
                path_id = getattr(g, "path_id", "") or ""
                if path_id:
                    g.repair_hint += f" (path: {path_id})"
    elif gate == "path_not_reached":
        # Diagnostic refutation: try to identify the frontier where
        # execution stopped, instead of always refuting the earliest gate.
        raw_output = ""
        result = getattr(state, "last_verification_result", None)
        if isinstance(result, dict):
            raw_output = str(result.get("raw_output") or result.get("vul_stderr") or "")

        # Check which chain nodes appear in the server output
        reached_funcs = set()
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        if raw_output:
            for node in nodes:
                if node.function and node.function in raw_output:
                    reached_funcs.add(node.function)

        # Find the frontier: first unreached node after a reached one
        target_gate = None
        if reached_funcs and nodes:
            sorted_nodes = sorted(nodes, key=lambda n: n.order)
            frontier_node = None
            for node in sorted_nodes:
                if node.function not in reached_funcs:
                    # Check if any earlier node was reached
                    earlier_reached = any(
                        n.function in reached_funcs
                        for n in sorted_nodes if n.order < node.order
                    )
                    if earlier_reached:
                        frontier_node = node
                        break
            if frontier_node:
                # Refute gates at the frontier node, preferring
                # reachability-role gates (trigger-role gates don't
                # affect path reachability).
                frontier_gates = [(i, g) for i, g in open_gates
                                  if g.node_order == frontier_node.order]
                target_gate = None
                # Prefer reachability-role
                for i, g in frontier_gates:
                    if getattr(g, "role", "reachability") == "reachability":
                        target_gate = (i, g)
                        break
                # Fallback to any role
                if target_gate is None and frontier_gates:
                    target_gate = frontier_gates[0]

        if target_gate:
            target_gate[1].status = "refuted"
            cond = target_gate[1].required_condition or "unknown condition"
            reached_str = ", ".join(sorted(reached_funcs)[:3]) if reached_funcs else "entry"
            # Find the frontier node's function name
            frontier_func = ""
            for n in nodes:
                if n.order == target_gate[1].node_order:
                    frontier_func = n.function
                    break
            target_gate[1].repair_hint = (
                f"Input reached [{reached_str}] but did not reach "
                f"{frontier_func or 'the next node'}. "
                f"Condition to satisfy: {cond}. "
                f"Fix the corresponding field in your PoC."
            )
            target_gate[1].evidence = f"Refuted by path_not_reached (frontier diagnosed)"
        elif open_gates:
            # No crash trace to determine frontier. Use "questioned"
            # instead of "refuted" — the gate might be correct, the
            # agent just couldn't construct a PoC that satisfies it.
            earliest = min(open_gates, key=lambda x: x[1].node_order)
            earliest[1].status = "questioned"
            cond = earliest[1].required_condition or ""
            hint = (
                "Path not reached but no crash trace to determine frontier. "
                "This gate may be correct — confirm or adjust."
            )
            if poc_hex:
                hint += f" Your PoC starts with: {poc_hex}."
            if cond:
                hint += f" Required: {cond}. Consider if this condition is truly necessary."
            else:
                hint += (
                    " READ the code at this point to find the exact condition, "
                    "then use record_gate to capture it."
                )
            earliest[1].repair_hint = hint
            earliest[1].evidence = "Questioned by path_not_reached (no crash evidence)"
    elif gate == "trigger_wrong_signature":
        # ASAN corruption detected but wrong crash type — the path WAS
        # reached but the trigger is wrong.  Don't refute path gates;
        # mark the sink's bounds/value gate as needing refinement.
        # Prefer trigger-role gates over reachability-role gates.
        sink_order = max(
            (n.order for n in state.call_chain_nodes if n.role == "sink"), default=0
        )
        target = None
        # Prefer trigger-role
        for i, g in open_gates:
            if (g.gate_type in ("bounds_gate", "value_gate")
                    and getattr(g, "role", "reachability") == "trigger"
                    and g.node_order == sink_order):
                target = g
                break
        # Fallback to any role
        if target is None:
            for i, g in open_gates:
                if (g.gate_type in ("bounds_gate", "value_gate")
                        and g.node_order == sink_order):
                    target = g
                    break
        if target is not None:
            target.status = "refuted"
            target.repair_hint = "Trigger reached but wrong crash signature — refine overflow size/offset"
            target.evidence = "Refuted by trigger_wrong_signature"
    elif gate == "trigger_wrong_location":
        # Crash in unexpected location — dispatch gates are wrong
        for i, g in open_gates:
            if g.gate_type == "dispatch_gate":
                g.status = "refuted"
                g.repair_hint = "Input routed to wrong code path — fix the dispatch field in PoC"
                g.evidence = f"Refuted by trigger_wrong_location"
    elif gate == "wrong_trigger":
        # Non-ASAN crash or crash without type — input reached the code
        # but didn't satisfy the trigger condition. Refute the first
        # open value_gate or bounds_gate at the sink node.
        # Prefer trigger-role gates over reachability-role gates.
        sink_order = max(
            (n.order for n in state.call_chain_nodes if n.role == "sink"),
            default=0,
        )
        target = None
        # Prefer trigger-role
        for i, g in open_gates:
            if (g.gate_type in ("value_gate", "bounds_gate")
                    and getattr(g, "role", "reachability") == "trigger"
                    and g.node_order == sink_order):
                target = g
                break
        # Fallback to any role
        if target is None:
            for i, g in open_gates:
                if (g.gate_type in ("value_gate", "bounds_gate")
                        and g.node_order == sink_order):
                    target = g
                    break
        if target is not None:
            target.status = "refuted"
            target.repair_hint = (
                "Input reached vulnerable code but trigger condition not met — "
                "adjust the trigger value/field in the PoC"
            )
            target.evidence = "Refuted by wrong_trigger"

    # Emit diagnostics for any gates whose status changed
    for g in state.call_chain_gates:
        if id(g) in pre_status and g.status != pre_status[id(g)]:
            diag_code = "gate_refuted" if g.status == "refuted" else "gate_questioned"
            diag_severity = "warning" if g.status == "refuted" else "info"
            state.constraint_diagnostics.append({
                "code": diag_code,
                "message": f"{g.description} → {g.repair_hint or 'status changed'}",
                "severity": diag_severity,
                "source_span": getattr(g, "source_span", {}) or {},
                "source": "feedback",
            })
    if len(state.constraint_diagnostics) > 32:
        state.constraint_diagnostics = state.constraint_diagnostics[-32:]
