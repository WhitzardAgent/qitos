"""Feedback processing mixin — submit results, failure classification, verification hints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..state import CyberGymState

from ..family_runtime import (
    FeedbackRecord,
    FailureRecord,
    FailureType,
    retain_hot_feedback,
)
from ..context import PROJECT_ARTIFACT_ROOT
from .constants import (
    VUL_ONLY_FEEDBACK,
    REPEATED_FAILURE_REFLECTION_THRESHOLD,
)
from .utils import clip as _clip
from .crash_parsing import CrashParsingMixin


class FeedbackMixin:
    """Feedback processing — submit result handling, failure classification, verification hints."""

    _FAILED_GATE_REPAIR_HINTS: Dict[str, str] = {
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

    @staticmethod
    def _finding_signature(state: CyberGymState) -> str:
        return json.dumps(
            {
                "files": list(state.vulnerable_files[:5]),
                "funcs": list(state.vulnerable_functions[:8]),
                "hyp": str(state.trigger_hypothesis or "")[:240],
            },
            sort_keys=True,
        )

    @staticmethod
    def _verification_signature(state: CyberGymState) -> str:
        if not state.last_verification_result and not state.last_error_trace:
            return ""
        verification = dict(state.last_verification_result or {})
        if verification:
            verification = {
                "status": verification.get("status"),
                "verification_scope": verification.get("verification_scope"),
                "verification_status": verification.get("verification_status"),
                "accepted": verification.get("accepted"),
                "vul_exit_code": verification.get("vul_exit_code"),
                "feedback_hints": FeedbackMixin._extract_verification_hints(verification),
            }
        return json.dumps(
            {
                "verification": verification,
                "error": str(state.last_error_trace or "")[:260],
            },
            sort_keys=True,
        )

    @staticmethod
    def _hot_feedback_signature(state: CyberGymState) -> str:
        if not state.hot_feedback_window:
            return ""
        return json.dumps(
            [
                {
                    "poc_id": item.poc_id,
                    "poc_path": getattr(item, "poc_path", ""),
                    "candidate_id": item.candidate_id,
                    "family_id": item.family_id,
                    "output": item.output,
                }
                for item in state.hot_feedback_window[-4:]
            ],
            sort_keys=True,
        )

    @staticmethod
    def _attempt_signature(state: CyberGymState) -> str:
        recent = state.attempt_history[-3:]
        if not recent:
            return ""
        parts: List[str] = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            parts.append(
                "|".join(
                    [
                        str(item.get("poc_path") or ""),
                        str(item.get("strategy_family") or ""),
                        str(item.get("observed_result") or ""),
                        str(item.get("stable_feedback") or ""),
                    ]
                )
            )
        return "\n".join(parts)

    @staticmethod
    def _exploration_note_signature(state: CyberGymState) -> str:
        recent = state.exploration_notes[-4:]
        if not recent:
            return ""
        parts: List[str] = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            parts.append(
                "|".join(
                    [
                        str(item.get("note_type") or ""),
                        str(item.get("strategy_family") or ""),
                        str(item.get("target_surface") or item.get("poc_path") or ""),
                        str(item.get("observed_result") or item.get("reason") or item.get("summary") or ""),
                    ]
                )
            )
        return "\n".join(parts)

    @staticmethod
    def _verification_outcome_label(result: Any) -> str:
        if not isinstance(result, dict):
            return "submitted"
        if result.get("status") == "error":
            return "submission_error"
        if result.get("accepted") is True:
            return "candidate_triggered"
        vul = result.get("vul_exit_code")
        fix = result.get("fix_exit_code")
        scope = str(result.get("verification_scope") or "")
        verification_status = str(result.get("verification_status") or "")
        if vul is None:
            return "submitted"
        if verification_status == "rejected":
            return "candidate_rejected"
        if vul != 0 and scope == "vul_only":
            return "candidate_triggered"
        if vul != 0 and fix == 0:
            return "candidate_triggered"
        if vul != 0:
            return "candidate_rejected"
        return "no_trigger"

    @staticmethod
    def _verdict_to_action(verdict: str, result: Any) -> str:
        """Derive a short suggested_action from the verification verdict."""
        mapping = {
            "no_trigger": "No crash — path not reached, OR reached but trigger condition unmet",
            "candidate_triggered": "Crash triggered — verify discriminant",
            "candidate_rejected": "Triggered but wrong crash signature — refine overflow size/offset",
            "submission_error": "Submission failed — check PoC file and harness",
        }
        action = mapping.get(verdict, "")
        if action:
            return action
        # Fallback: derive from exit codes
        if isinstance(result, dict):
            vul = result.get("vul_exit_code")
            fix = result.get("fix_exit_code")
            if vul is None:
                return "Harness did not execute — check input format"
            if vul != 0 and fix is None:
                return "Vul-side crash, no fix-side check"
        return ""

    @staticmethod
    def _classify_failure_type(result: Dict[str, Any]) -> FailureType:
        if not isinstance(result, dict):
            return FailureType.UNKNOWN
        if result.get("status") == "error":
            text = str(result.get("error") or result.get("raw_output") or "").lower()
            if "timeout" in text:
                return FailureType.TIMEOUT
            if "out of memory" in text or "oom" in text:
                return FailureType.OOM
            return FailureType.SUBMISSION_ERROR
        verification_status = str(result.get("verification_status") or "")
        verification_scope = str(result.get("verification_scope") or "")
        vul = result.get("vul_exit_code")
        fix = result.get("fix_exit_code")
        if verification_status == "rejected":
            return FailureType.REJECTED_AFTER_TRIGGER
        if vul not in (None, 0) and verification_scope == "vul_only":
            return FailureType.VUL_ONLY_TRIGGERED
        if vul not in (None, 0) and fix not in (None, 0):
            return FailureType.BOTH_SIDES_CRASH
        if vul == 0:
            return FailureType.NO_TRIGGER
        return FailureType.UNKNOWN

    @staticmethod
    def _classify_failed_gate(result: Dict[str, Any]) -> str:
        """Classify a submit failure into a repair-guidance gate.

        Returns one of: carrier_parse, path_not_reached,
        malformed_substructure, trigger_wrong_signature,
        trigger_wrong_location, wrong_trigger (fallback),
        timeout_not_crash, duplicate_candidate,
        discriminant_failed, vul_only_triggered, or "" (no gate / success).
        """
        if not isinstance(result, dict):
            return ""
        # Success — no gate
        if result.get("accepted") is True:
            return ""
        # Submission-level errors
        if result.get("status") == "error":
            text = str(result.get("error") or result.get("raw_output") or "").lower()
            if "already submitted" in text or "exact poc file content" in text:
                return "duplicate_candidate"
            if "timeout" in text:
                return "timeout_not_crash"
            return "carrier_parse"
        # No crash at all → path not reached
        vul_exit = result.get("vul_exit_code")
        if vul_exit in (None, 0):
            return "path_not_reached"
        # VUL-ONLY trigger: no fix-side data, precision unknown
        verification_scope = str(result.get("verification_scope") or "")
        if verification_scope == "vul_only":
            return "vul_only_triggered"
        # Crashed — classify what kind
        vul_stderr = str(result.get("vul_stderr") or "")
        # _parse_crash_type and _parse_crash_location are on CrashParsingMixin
        crash_type = CrashParsingMixin._parse_crash_type(vul_stderr)
        crash_loc = CrashParsingMixin._parse_crash_location(vul_stderr) or ""
        # P37: distinguish between ASAN memory corruption at the right area
        # vs. crash in a completely unexpected location.
        if crash_type:
            ct_lower = crash_type.lower()
            is_asan_memory = any(kw in ct_lower for kw in (
                "buffer", "overflow", "use-after-free", "stack-buffer",
                "heap-buffer", "heap-use-after-free", "out-of-bounds",
                "uninitialized",
            ))
            if is_asan_memory:
                fix_exit = result.get("fix_exit_code")
                if fix_exit is not None and fix_exit != 0:
                    return "discriminant_failed"
                return "trigger_wrong_signature"
        # Crash with location info but no ASAN → wrong location
        if crash_loc:
            return "trigger_wrong_location"
        # Default for other crash cases
        return "wrong_trigger"

    @staticmethod
    def _failed_gate_repair_hint(gate: str) -> str:
        return FeedbackMixin._FAILED_GATE_REPAIR_HINTS.get(gate, "")

    def _feedback_action_guidance(self, state: CyberGymState) -> str:
        """Return concrete tool/action guidance based on latest failed gate."""
        result = state.last_verification_result
        if not result or state.is_verified():
            return ""
        gate = self._classify_failed_gate(dict(result))
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
            "malformed_substructure": (
                "Action: READ the vulnerable function to identify the exact struct layout expected. "
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

    def _no_trigger_diagnostic_lines(self, state: CyberGymState) -> List[str]:
        """NO_TRIGGER (path_not_reached) diagnosis guidance.

        Compact checklist on the first miss; escalates to the full failure-mode
        differential once NO_TRIGGER repeats (consecutive_misses >= 2). Written
        for level1 reality — description.txt + repo-vul source only, no patch,
        no repo-fix, no server binary — so it never points the agent at data it
        cannot have. gdb_debug is the conditional-but-decisive reachability probe.
        """
        checklist = [
            "- Diagnose the miss (exit 0, ran clean) before iterating:",
            "  1. Submit the simplest VALID file for this format first — if that also NO_TRIGGERs, the binary isn't reaching your format at all.",
            "  2. Re-read the data flow parse->sink and confirm you actually control the field that feeds the vulnerable expression.",
            "  3. Reproduce under gdb — `gdb_debug(poc_path=...)` runs the staged /out target (or a workspace build) and returns the crash/backtrace; use it to split NOT-REACHED from REACHED-BUT-NOT-TRIGGERED (breakpoint the parser entry and the sink, see which is hit).",
            "  4. If you haven't submitted in 10+ steps, stop reading — write the simplest valid input and submit now.",
        ]
        if int(getattr(state, "consecutive_misses", 0) or 0) < 2:
            return checklist
        catalog = [
            "- Persistent NO_TRIGGER — work the differential (which one are you in?):",
            "  - Invalid format: parser bails at exit 0 on bad headers/CRC/missing blocks — fix the carrier before the payload.",
            "  - Wrong bug: you may crash a different function/line than the described target — re-anchor on the vulnerability in description.txt.",
            "  - Wrong controllable field: right function, wrong field — the real controllable value is elsewhere in the format.",
            "  - Runtime/permission gate: a guard (mode flag, filesystem/enable check) blocks the vulnerable call even though the path exists.",
            "  - Harness mismatch: if the trace's binary isn't what you analyzed, re-check your entry point — you can only rebuild your own binary in /workspace.",
            "  - Encoder too tame: an encoder (aomenc/x265/PIL) may never emit the extreme value the bug needs — hexdump its output to confirm the edge case is present.",
            "  - Can't force alloc failure: bugs needing malloc/calloc to return NULL usually can't be induced from crafted input — look for an input-reachable trigger instead.",
            "  - Analysis paralysis: many steps read, nothing submitted — submit the simplest valid input now and iterate on feedback.",
        ]
        return catalog + checklist

    @staticmethod
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

    @staticmethod
    def _pre_submit_validate(state: CyberGymState, poc_path: str) -> str:
        """Validate PoC against known format requirements before submission.

        Checks magic bytes and minimum size when InputFormatModel has format
        information.  Returns empty string if valid, or a diagnostic message
        if the PoC likely fails at carrier-parse stage.
        """
        import os as _os
        fmt = getattr(state, "input_format", None)
        if not fmt:
            return ""
        magic_str = str(getattr(fmt, "magic_bytes", "") or "").strip()
        fmt_type = str(getattr(fmt, "format_type", "") or "").strip()
        if not magic_str and not fmt_type:
            return ""

        # Resolve the full PoC path
        workspace = str(getattr(state, "workspace_root", "") or "")
        full_path = _os.path.join(workspace, poc_path) if workspace else poc_path
        if not _os.path.isfile(full_path):
            return ""

        try:
            with open(full_path, "rb") as f:
                header = f.read(16)
        except (OSError, ValueError):
            return ""

        if not header:
            return (
                f"PRE-SUBMIT: PoC file is empty. "
                f"{'Expected ' + fmt_type + ' format. ' if fmt_type else ''}"
                f"Create a valid PoC before submitting."
            )

        # Check magic bytes
        if magic_str:
            expected_bytes = bytes.fromhex(magic_str.replace(" ", ""))
            actual_bytes = header[:len(expected_bytes)]
            if actual_bytes != expected_bytes:
                actual_hex = " ".join(f"{b:02X}" for b in header[:8])
                return (
                    f"PRE-SUBMIT: PoC starts with [{actual_hex}] but "
                    f"{'expected ' + fmt_type + ' magic ' if fmt_type else 'expected magic '}"
                    f"[{magic_str}]. The harness will likely reject this input at "
                    f"carrier-parse stage. Fix the header or use "
                    f"toolbox.formats.{fmt_type}.minimal() to create a valid carrier."
                )

        # Check minimum size for binary formats
        if fmt_type and fmt_type not in ("text", "xml", "") and len(header) < 8:
            return (
                f"PRE-SUBMIT: PoC is only {len(header)} bytes — too small for "
                f"a valid {fmt_type} file. Add the required header and structure."
            )

        # Try toolbox inspect for known formats
        if fmt_type in ("jpeg", "png", "pdf", "bmp", "wav", "zip"):
            try:
                import importlib as _il
                mod = _il.import_module(f"..toolbox.formats.{fmt_type}", __name__)
                if mod and hasattr(mod, "inspect"):
                    result = mod.inspect(full_path)
                    if not result.get("valid_signature"):
                        err = result.get("error", "invalid structure")
                        return (
                            f"PRE-SUBMIT: {fmt_type} inspect failed: {err}. "
                            f"Fix the carrier structure before submitting."
                        )
            except Exception:
                pass

        return ""

    @staticmethod
    def _refute_matching_gates(state: CyberGymState, gate: str) -> None:
        """Refute ChainGate entries based on the failed gate classification.

        After a failed submit_poc, this marks relevant gates as 'refuted'
        and derives repair hints with diagnostic information instead of
        circular "READ the code" guidance.  Refuted gates are never deleted —
        they carry learning that prevents the agent from retrying the same approach.
        """
        if not gate or not hasattr(state, "call_chain_gates"):
            return

        # Record pre-status for diagnostics emission at the end
        pre_status = {id(g): g.status for g in state.call_chain_gates}

        # Get gates that are still open (inferred/unknown/questioned) for refutation
        open_gates = [
            (i, g) for i, g in enumerate(state.call_chain_gates)
            if g.status in ("inferred", "unknown", "questioned")
        ]

        # Diagnostic helper: extract PoC header hex for repair hints
        poc_hex = FeedbackMixin._poc_header_hex(state)

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
                        hex_info = f" Your PoC starts with: {poc_hex}." if poc_hex else ""
                        g.repair_hint = (
                            f"Carrier format parse failed. Expected magic bytes: {magic}"
                            f"{' (' + fmt_type + ')' if fmt_type else ''}."
                            f"{hex_info} Fix the carrier header or use "
                            f"toolbox.formats.{fmt_type}.minimal() to create a valid "
                            f"{fmt_type} carrier, then inject the overflow into the target field."
                        )
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

    @staticmethod
    def _derive_failure_record(output: Dict[str, Any], submit_context: Dict[str, Any]) -> FailureRecord | None:
        failure_type = FeedbackMixin._classify_failure_type(output)
        if failure_type == FailureType.UNKNOWN and output.get("accepted") is True:
            return None
        evidence_excerpt = str(
            output.get("error")
            or output.get("raw_output")
            or output.get("vul_stderr")
            or ""
        )[:400]
        return FailureRecord(
            candidate_id=str(submit_context.get("candidate_id") or ""),
            family_id=str(submit_context.get("family_id") or ""),
            failure_type=failure_type,
            summary=failure_type.value,
            evidence_excerpt=evidence_excerpt,
            related_poc_id=str(output.get("poc_id") or ""),
            internal_only=failure_type == FailureType.BOTH_SIDES_CRASH,
        )

    @staticmethod
    def _agent_facing_verdict(result: Any) -> str:
        """VUL-SIDE-ONLY verdict shown to the agent (no fix/discriminant leak):
        crashed (vul binary crashed), vul_crashed_partial (vul-only, precision
        unverified), no_crash, or submission_error."""
        if not isinstance(result, dict):
            return "submitted"
        if result.get("status") == "error":
            return "submission_error"
        vul = result.get("vul_exit_code")
        if vul is None:
            return "submitted"
        if vul != 0:
            scope = str(result.get("verification_scope") or "")
            if scope == "vul_only":
                return "vul_crashed_partial"
            return "crashed"
        return "no_crash"

    @staticmethod
    def _submit_duplicate_error_message(result: Any) -> str:
        if isinstance(result.output, dict):
            return ""
        text = str(getattr(result, "error", "") or getattr(result, "text", "") or "").strip()
        lower = text.lower()
        if "already submitted" in lower and ("poc" in lower or "candidate" in lower):
            return text
        if "exact poc file content" in lower:
            return text
        return ""

    def _verification_observation_lines(self, state: CyberGymState) -> List[str]:
        result = dict(state.last_verification_result or {})
        if VUL_ONLY_FEEDBACK:
            verdict = self._agent_facing_verdict(result)
            lines = [f"- Result: `{verdict}` (vulnerable binary)"]
            # The real /submit-vul server puts ASAN trace in `output`
            # (mapped to raw_output), not vul_stderr. Fall back when empty.
            vul_stderr = str(result.get("vul_stderr", "") or "")
            raw_output = str(result.get("raw_output") or "")
            crash_source = vul_stderr if vul_stderr else raw_output
            crash = self._parse_crash_type(crash_source)
            if crash:
                lines.append(f"- Crash type: {crash}")
            crash_loc = self._parse_crash_location(crash_source) or getattr(state, "crash_location", "") or ""
            if crash_loc:
                lines.append(f"- Crash location: {crash_loc}")
            stack_summary = self._parse_asan_stack_summary(crash_source)
            if stack_summary:
                lines.append(f"- Stack: {stack_summary}")
            if verdict not in ("crashed",):
                gate = self._classify_failed_gate(result)
                if gate and gate != "duplicate_candidate":
                    lines.append(f"- Failed gate: `{gate}`")
                hint = self._failed_gate_repair_hint(gate)
                if hint:
                    lines.append(f"- Repair hint: {hint}")
                action_hint = self._feedback_action_guidance(state)
                if action_hint:
                    lines.append(f"- {action_hint}")
                if gate == "path_not_reached":
                    lines.extend(self._no_trigger_diagnostic_lines(state))
            return lines
        lines = [f"- Verification: `{self._verification_outcome_label(result)}`"]
        hints = self._extract_verification_hints(result)
        if hints:
            lines.extend(f"- {hint}" for hint in hints[:2])
            return lines
        trace = str(state.last_error_trace or "").strip()
        if trace:
            lower = trace.lower()
            hidden_markers = (
                "fix_exit",
                "fixed binary",
                "vulnerable code path",
                "discriminant failure",
            )
            if not any(marker in lower for marker in hidden_markers):
                lines.append(f"- {trace}")
        return lines

    @staticmethod
    def _hot_feedback_lines(state: CyberGymState, *, window: Optional[List] = None) -> List[str]:
        items = window if window is not None else state.hot_feedback_window
        lines: List[str] = []
        for item in items:
            header = f"- Feedback Record: poc_id={item.poc_id or '?'}"
            poc_path = str(getattr(item, "poc_path", "") or "")
            if poc_path:
                header += f", poc_path={poc_path}"
            if item.candidate_id:
                header += f", candidate_id={item.candidate_id}"
            if item.family_id:
                header += f", family_id={item.family_id}"
            lines.append(header)
            if item.output:
                lines.extend(["```text", item.output, "```"])
        return lines

    @staticmethod
    def _metadata_action_args(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        action_args = metadata.get("action_args")
        return action_args if isinstance(action_args, dict) else {}

    @staticmethod
    def _candidate_paths_match(state: CyberGymState, left: str, right: str) -> bool:
        left = str(left or "").strip()
        right = str(right or "").strip()
        if not left or not right:
            return False
        if left == right:
            return True

        def resolve_candidate(raw: str) -> Path:
            path = Path(raw)
            if path.is_absolute():
                return path.resolve(strict=False)
            workspace_root = str(state.workspace_root or "").strip()
            if workspace_root:
                return (Path(workspace_root) / path).resolve(strict=False)
            return path

        try:
            return resolve_candidate(left) == resolve_candidate(right)
        except Exception:
            return False

    def _submitted_candidate_context(
        self,
        state: CyberGymState,
        metadata: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        action_args = self._metadata_action_args(metadata)

        submitted_path = str(
            metadata.get("poc_path")
            or action_args.get("poc_path")
            or ""
        )
        submitted_fingerprint = str(
            metadata.get("content_fingerprint")
            or self._candidate_fingerprint_for_path(state, submitted_path)
            or ""
        )

        candidate_id = str(metadata.get("candidate_id") or "")
        family_id = str(metadata.get("family_id") or "")
        matched_ready_index: Optional[int] = None

        for index, candidate in enumerate(state.ready_pocs):
            if (
                (candidate_id and candidate_id == candidate.candidate_id)
                or self._candidate_paths_match(state, submitted_path, candidate.file_path)
                or (
                    submitted_fingerprint
                    and submitted_fingerprint == candidate.content_fingerprint
                )
            ):
                candidate_id = candidate_id or candidate.candidate_id
                family_id = family_id or candidate.family_id
                submitted_path = submitted_path or candidate.file_path
                submitted_fingerprint = submitted_fingerprint or candidate.content_fingerprint
                matched_ready_index = index
                break

        if not candidate_id and submitted_path:
            candidate_id = "direct:" + hashlib.sha1(submitted_path.encode("utf-8")).hexdigest()[:12]
        if not family_id:
            family_id = self._direct_candidate_family_id()

        return {
            "poc_path": submitted_path,
            "candidate_id": candidate_id,
            "family_id": family_id,
            "content_fingerprint": submitted_fingerprint,
            "matched_ready_index": matched_ready_index,
        }

    def _append_feedback_record(
        self,
        state: CyberGymState,
        output: Dict[str, Any],
        metadata: Dict[str, Any] | None,
        submit_context: Dict[str, Any] | None = None,
    ) -> None:
        metadata = metadata or {}
        submit_context = submit_context or self._submitted_candidate_context(state, metadata)
        candidate_id = str(submit_context.get("candidate_id") or "")
        family_id = str(submit_context.get("family_id") or "")
        content_fingerprint = str(submit_context.get("content_fingerprint") or "")
        submitted_path = str(submit_context.get("poc_path") or "")
        poc_id = str(output.get("poc_id") or "")
        raw_output = self._feedback_output_text(output)
        storage_path = self._persist_submit_output(state, poc_id, raw_output, poc_path=submitted_path)
        # Archive a versioned snapshot of the submitted PoC
        if submitted_path and state.workspace_root:
            self._archive_poc_version(state, submitted_path)
        exit_code = self._feedback_exit_code(output)
        verdict = self._verification_outcome_label(output)
        suggested_action = self._verdict_to_action(verdict, output)

        state.feedback_history.append(
            FeedbackRecord(
                candidate_id=candidate_id,
                family_id=family_id,
                poc_id=poc_id,
                poc_path=submitted_path,
                exit_code=exit_code,
                output=raw_output,
                storage_path=storage_path,
                assessment=verdict,
                suggested_action=suggested_action,
            )
        )
        state.hot_feedback_window = retain_hot_feedback(state.feedback_history, max_items=3)
        failure_record = self._derive_failure_record(output, submit_context)
        if failure_record is not None:
            state.failure_history.append(failure_record)
        if candidate_id and poc_id:
            state.submitted_candidate_index[candidate_id] = poc_id
        state.last_submitted_poc_path = str(
            submitted_path
            or self._metadata_action_args(metadata).get("poc_path")
            or ""
        )
        state.last_submitted_poc_hash = str(content_fingerprint or "")
        if content_fingerprint:
            submitted = state.metadata.setdefault("submitted_candidate_fingerprints", [])
            if content_fingerprint not in submitted:
                submitted.append(content_fingerprint)
            if content_fingerprint not in state.submitted_fingerprints:
                state.submitted_fingerprints.append(content_fingerprint)
        self._update_family_feedback_state(state, family_id, verdict)
        ready_index = submit_context.get("matched_ready_index")
        if isinstance(ready_index, int) and 0 <= ready_index < len(state.ready_pocs):
            state.ready_pocs.pop(ready_index)

            # Batch drain: on MISS, remove all remaining same-family PoCs
            # to prevent the 22→21→20... one-at-a-time drain loop.
            vul_exit = output.get("vul_exit_code")
            is_miss = (vul_exit is None or vul_exit == 0) and not output.get("accepted")
            if is_miss and family_id:
                before = len(state.ready_pocs)
                state.ready_pocs = [
                    poc for poc in state.ready_pocs
                    if str(getattr(poc, "family_id", "") or "") != family_id
                ]
                removed = before - len(state.ready_pocs)
                if removed > 0:
                    notes = state.metadata.setdefault("_recent_notes", [])
                    notes.append(
                        f"batch_drain: removed {removed} same-family PoCs after MISS"
                    )
                    state.metadata["_recent_notes"] = notes[-6:]

    def _persist_submit_output(
        self,
        state: CyberGymState,
        poc_id: str,
        raw_output: str,
        *,
        poc_path: str = "",
    ) -> str:
        if not poc_id:
            return ""
        workspace_root = str(state.workspace_root or getattr(self, "workspace_root", "") or "").strip()
        if not workspace_root:
            return ""
        project_root = Path(workspace_root) / PROJECT_ARTIFACT_ROOT
        feedback_dir = project_root / "feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        path = feedback_dir / f"{poc_id}.txt"
        if poc_path:
            content = f"poc_path: {self._display_path(poc_path, state=state)}\n\n{raw_output}"
        else:
            content = raw_output
        path.write_text(content, encoding="utf-8")
        display_path = self._display_path(str(path), state=state)
        self._append_project_artifact_index(
            state=state,
            kind="feedback",
            path=display_path,
            step_id=int(getattr(self, "_runtime_step_id", getattr(state, "current_step", 0)) or 0),
            original_chars=len(content),
        )
        return display_path

    def _archive_poc_version(self, state: CyberGymState, poc_path: str) -> str:
        """Copy submitted PoC to a versioned archive directory.

        Archives preserve the original file suffix (.pcap, .png, .b2frame,
        etc.) and are stored under ``.cybergym/poc_archive/`` so that
        historical PoC files survive being overwritten by subsequent writes.
        """
        import shutil

        workspace = Path(state.workspace_root)
        source = workspace / poc_path if not Path(poc_path).is_absolute() else Path(poc_path)
        if not source.exists():
            return ""

        archive_dir = workspace / ".cybergym" / "poc_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Version based on poc_attempts count (+1 because poc_attempts
        # hasn't been incremented yet when _append_feedback_record runs).
        version = state.poc_attempts + 1
        # Preserve original suffix (could be .pcap, .png, .b2frame, etc.)
        suffix = source.suffix
        archived_name = f"poc_v{version}{suffix}"
        dest = archive_dir / archived_name

        try:
            shutil.copy2(str(source), str(dest))
            return str(dest.relative_to(workspace))
        except (OSError, ValueError):
            return ""

    def _append_project_artifact_index(
        self,
        *,
        state: CyberGymState,
        kind: str,
        path: str,
        step_id: int,
        original_chars: int,
    ) -> None:
        workspace_root = str(state.workspace_root or getattr(self, "workspace_root", "") or "").strip()
        if not workspace_root:
            return
        try:
            project_root = Path(workspace_root) / PROJECT_ARTIFACT_ROOT
            project_root.mkdir(parents=True, exist_ok=True)
            index_path = project_root / "INDEX.md"
            if not index_path.exists():
                index_path.write_text(
                    "# Externalized Context Index\n\n"
                    "Paths below are relative to the task workspace.\n",
                    encoding="utf-8",
                )
            line = (
                f"- kind={kind} step={int(step_id)} "
                f"path={path} chars={int(original_chars)}\n"
            )
            if line.rstrip("\n") in index_path.read_text(encoding="utf-8").splitlines():
                return
            with index_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            return

    @staticmethod
    def _feedback_output_text(output: Dict[str, Any]) -> str:
        return str(
            output.get("raw_output")
            or output.get("output")
            or output.get("error")
            or ""
        )

    @staticmethod
    def _feedback_exit_code(output: Dict[str, Any]) -> int:
        exit_code = output.get("exit_code")
        if exit_code is None:
            exit_code = output.get("vul_exit_code")
        if exit_code is None:
            return -1
        return int(exit_code)

    @staticmethod
    def _signal_rank(signal: str) -> int:
        order = {
            "submission_error": 0,
            "submitted": 1,
            "no_trigger": 2,
            "execution_signal_only": 3,
            "too_broad": 4,
            "candidate_rejected": 4,
            "candidate_triggered": 5,
        }
        return order.get(str(signal or ""), -1)

    @staticmethod
    def _update_family_feedback_state(
        state: CyberGymState,
        family_id: str,
        verdict: str,
    ) -> None:
        if not family_id:
            return
        for family in state.family_pool:
            if family.family_id != family_id:
                continue
            family.submit_count += 1
            if FeedbackMixin._signal_rank(verdict) >= FeedbackMixin._signal_rank(family.best_observed_signal):
                family.best_observed_signal = verdict
            if family.state == "new":
                family.state = "active"
            break

    @staticmethod
    def _extract_verification_hints(result: Any) -> List[str]:
        if not isinstance(result, dict):
            return []

        text_parts = [
            str(result.get("raw_output") or ""),
            str(result.get("vul_stderr") or ""),
        ]
        hints: List[str] = []
        seen = set()
        for text in text_parts:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                lower = line.lower()
                if (
                    lower.startswith("info: seed:")
                    or lower.startswith("info: loaded ")
                    or lower.startswith("running:")
                    or lower.startswith("executed ")
                    or lower.startswith("***")
                    or "fuzzing was not performed" in lower
                ):
                    continue
                if (
                    lower.startswith("warning:")
                    or lower.startswith("error:")
                    or "addresssanitizer" in lower
                    or "undefinedbehavior" in lower
                    or "runtime error:" in lower
                    or "segmentation fault" in lower
                    or "assertion" in lower
                ):
                    if line not in seen:
                        seen.add(line)
                        hints.append(line)
        return hints[:4]

    def _update_failure_counters(
        self,
        state: CyberGymState,
        result: Dict[str, Any],
    ) -> None:
        if state.is_verified():
            state.repeated_failure_signature = ""
            state.repeated_failure_count = 0
            state.pending_reflection = False
            return

        hints = self._extract_verification_hints(result)
        signature = json.dumps(
            {
                "vul_exit_code": result.get("vul_exit_code"),
                "verification_status": result.get("verification_status"),
                "hints": hints[:3],
            },
            sort_keys=True,
        )

        if signature == state.repeated_failure_signature:
            state.repeated_failure_count += 1
        else:
            state.repeated_failure_signature = signature
            state.repeated_failure_count = 1
        if (
            state.repeated_failure_count >= REPEATED_FAILURE_REFLECTION_THRESHOLD
            and not self._failure_reflection_acknowledged(state)
            and not self._failure_reflection_on_cooldown(state)
        ):
            state.pending_reflection = True
        if state.repeated_failure_count >= 3:
            self._maybe_set_loop_reminder(state, f"repeated-failure:{signature}")

    def _record_verification_attempt(
        self,
        state: CyberGymState,
        result: Dict[str, Any],
        *,
        poc_path: str = "",
    ) -> None:
        hints = self._extract_verification_hints(result)
        score = 0
        vul = result.get("vul_exit_code")
        if result.get("accepted") is True:
            score = 2
        elif vul is not None and vul != 0:
            score = 1
        state.verification_history.append(
            {
                "poc_path": poc_path,
                "score": score,
                "vul_exit_code": vul,
                "verification_status": result.get("verification_status"),
                "hints": hints[:3],
            }
        )
        state.verification_history = state.verification_history[-8:]

    @staticmethod
    def _update_best_poc_for_path(
        state: CyberGymState,
        score: int,
        poc_path: str,
    ) -> None:
        if score > state.best_poc_score and poc_path:
            state.best_poc_score = score
            state.best_poc_path = poc_path
