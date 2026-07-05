"""Feedback processing mixin — submit results, failure classification, verification hints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ...state import CyberGymState

from ...family_runtime import (
    FailureRecord,
    FailureType,
)
from ..core.crash_parsing import CrashParsingMixin


class FeedbackMixin:
    """Feedback processing — submit result handling, failure classification, verification hints."""

    # Kept for backward compat; canonical copy lives in gate_refutation.py
    _FAILED_GATE_REPAIR_HINTS: Dict[str, str] = {}

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
        return "no_crash_unknown"

    @staticmethod
    def _verdict_to_action(verdict: str, result: Any) -> str:
        """Derive a short suggested_action from the verification verdict."""
        mapping = {
            "no_trigger": "No crash — reachability and trigger satisfaction are both unknown",
            "no_crash_unknown": "No crash — reachability and trigger satisfaction are both unknown",
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
            return FailureType.NO_CRASH_UNKNOWN
        return FailureType.UNKNOWN

    @staticmethod
    def _classify_failed_gate(result: Dict[str, Any]) -> str:
        """Classify a submit failure into a repair-guidance gate.

        Returns one of: carrier_parse, no_crash_unknown, path_not_reached,
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
        # No crash at all. This is intentionally NOT path_not_reached: the
        # same observation can also mean the path was reached but the value,
        # size, state, or checksum did not satisfy the vulnerable condition.
        vul_exit = result.get("vul_exit_code")
        if vul_exit in (None, 0):
            return "no_crash_unknown"
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
        from .gate_refutation import failed_gate_repair_hint
        return failed_gate_repair_hint(gate)

    def _feedback_action_guidance(self, state: CyberGymState) -> str:
        from .gate_refutation import feedback_action_guidance
        return feedback_action_guidance(self, state)

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

        Uses the PoC sanity checker (poc_sanity.py) for comprehensive checks:
        generic bytes, corpus-aware delta, and format-specific carrier sanity.
        Also runs consistency guard for harness/format/scope alignment.

        Returns empty string if valid, or a diagnostic message if the PoC
        likely fails at carrier-parse stage.  Messages starting with
        "CARRIER_SANITY_FAIL:" indicate hard blocks; "CONSISTENCY_BLOCK:"
        indicate harness/scope/format mismatches.
        """
        import os as _os

        # --- Phase 1: carrier sanity check (existing) ---
        from .consistency import pre_submit_sanity_check, run_consistency_guard
        sanity_result = pre_submit_sanity_check(state, poc_path)
        if sanity_result:
            return sanity_result

        # --- Phase 2: consistency guard ---
        return run_consistency_guard(state, poc_path)

    @staticmethod
    def _refute_matching_gates(state: CyberGymState, gate: str) -> None:
        from .gate_refutation import refute_matching_gates
        refute_matching_gates(state, gate)

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
        from .feedback_effect import verification_observation_lines
        return verification_observation_lines(self, state)

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
        from .submit_records import submitted_candidate_context
        return submitted_candidate_context(self, state, metadata)

    def _append_feedback_record(
        self,
        state: CyberGymState,
        output: Dict[str, Any],
        metadata: Dict[str, Any] | None,
        submit_context: Dict[str, Any] | None = None,
    ) -> None:
        from .submit_records import append_feedback_record
        append_feedback_record(self, state, output, metadata, submit_context)

    def _persist_submit_output(
        self,
        state: CyberGymState,
        poc_id: str,
        raw_output: str,
        *,
        poc_path: str = "",
    ) -> str:
        from .submit_records import persist_submit_output
        return persist_submit_output(self, state, poc_id, raw_output, poc_path=poc_path)

    def _archive_poc_version(self, state: CyberGymState, poc_path: str) -> str:
        from .submit_records import archive_poc_version
        return archive_poc_version(self, state, poc_path)

    def _append_project_artifact_index(
        self,
        *,
        state: CyberGymState,
        kind: str,
        path: str,
        step_id: int,
        original_chars: int,
    ) -> None:
        from .submit_records import append_project_artifact_index
        append_project_artifact_index(
            agent=self, state=state, kind=kind, path=path,
            step_id=step_id, original_chars=original_chars,
        )

    @staticmethod
    def _feedback_output_text(output: Dict[str, Any]) -> str:
        from .submit_records import feedback_output_text
        return feedback_output_text(output)

    @staticmethod
    def _feedback_exit_code(output: Dict[str, Any]) -> int:
        from .submit_records import feedback_exit_code
        return feedback_exit_code(output)

    @staticmethod
    def _signal_rank(signal: str) -> int:
        from .submit_records import signal_rank
        return signal_rank(signal)

    @staticmethod
    def _update_family_feedback_state(
        state: CyberGymState,
        family_id: str,
        verdict: str,
    ) -> None:
        from .submit_records import update_family_feedback_state
        update_family_feedback_state(state, family_id, verdict)

    @staticmethod
    def _extract_verification_hints(result: Any) -> List[str]:
        from .submit_records import extract_verification_hints
        return extract_verification_hints(result)

    def _update_failure_counters(
        self,
        state: CyberGymState,
        result: Dict[str, Any],
    ) -> None:
        from .submit_records import update_failure_counters
        update_failure_counters(self, state, result)

    def _record_verification_attempt(
        self,
        state: CyberGymState,
        result: Dict[str, Any],
        *,
        poc_path: str = "",
    ) -> None:
        from .submit_records import record_verification_attempt
        record_verification_attempt(self, state, result, poc_path=poc_path)

    @staticmethod
    def _update_best_poc_for_path(
        state: CyberGymState,
        score: int,
        poc_path: str,
    ) -> None:
        from .submit_records import update_best_poc_for_path
        update_best_poc_for_path(state, score, poc_path)

    # ------------------------------------------------------------------
    # Feedback effect — structured negative evidence generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_feedback_effect(
        state: CyberGymState,
        gate: str,
        result: Dict[str, Any],
        submit_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        from .feedback_effect import generate_feedback_effect
        return generate_feedback_effect(state, gate, result, submit_context)

    @staticmethod
    def _append_negative_evidence_from_feedback(
        state: CyberGymState,
        gate: str,
        feedback_effect: Dict[str, Any],
    ) -> str | None:
        from .feedback_effect import append_negative_evidence_from_feedback
        return append_negative_evidence_from_feedback(state, gate, feedback_effect)

    def _capture_feedback_fact(self, state: CyberGymState, output: Dict[str, Any]) -> None:
        from ..core.fact_extraction import append_capped_fact

        result = dict(output or {})
        verdict = self._verification_outcome_label(result)
        crash_type = self._parse_crash_type(str(result.get("vul_stderr", "") or result.get("raw_output", "") or ""))
        crash_location = self._parse_crash_location(str(result.get("vul_stderr", "") or result.get("raw_output", "") or ""))
        hints = self._extract_verification_hints(result)
        facts: List[str] = [f"verification: {verdict}"]
        # Failed gate classification
        gate = self._classify_failed_gate(result)
        if gate:
            facts.append(f"failed_gate: {gate}")
        latest_feedback = state.hot_feedback_window[-1] if state.hot_feedback_window else None
        if latest_feedback and latest_feedback.storage_path:
            facts.append(f"feedback_file: {self._display_path(latest_feedback.storage_path, state=state)}")
        poc_path = str(getattr(latest_feedback, "poc_path", "") if latest_feedback else "").strip()
        if poc_path:
            facts.append(f"feedback_poc_path: {self._display_path(poc_path, state=state)}")
        if crash_type:
            facts.append(f"crash_type: {crash_type}")
        if crash_location:
            facts.append(f"crash_location: {crash_location}")
        for hint in hints[:2]:
            facts.append(f"feedback_hint: {hint}")
        raw_output = str(result.get("raw_output") or result.get("output") or "").strip()
        if raw_output:
            facts.append(
                "feedback_analysis: latest full submit output is preserved; inspect Latest Hot Feedback or feedback_file before rereading code."
            )
        for fact in facts:
            state.durable_feedback_facts = append_capped_fact(
                state.durable_feedback_facts,
                fact,
            )
