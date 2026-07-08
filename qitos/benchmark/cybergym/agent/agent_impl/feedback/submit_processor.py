"""Submit PoC result processing — extracted from agent.py."""
from __future__ import annotations

from typing import Any

from ...state import CyberGymState
from ...agent_impl.core.constants import FAILURE_REFLECTION_ACK_KEY
from ..core.fact_extraction import append_capped_fact
from ..core.metadata_keys import (
    LAST_FEEDBACK_ACTION,
    LAST_FEEDBACK_ACTION_RESULT,
    LAST_PACK_FEEDBACK_ACTION,
)


def process_submit_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Process submit_poc tool result — extracted from _process_action_result.

    All agent mixin method calls are delegated through the ``agent`` parameter.
    """
    if not isinstance(output, dict):
        return
    submit_metadata = dict(result.metadata or {})
    for key in (
        "poc_path",
        "content_fingerprint",
        "candidate_id",
        "family_id",
    ):
        if key not in submit_metadata and output.get(key):
            submit_metadata[key] = output.get(key)
    submit_context = agent._submitted_candidate_context(state, submit_metadata)
    submitted_path = str(submit_context.get("poc_path") or "")
    agent._append_feedback_record(state, output, submit_metadata, submit_context)
    # BUGFIX (multi-submit in one round): a later no-crash submit must NOT
    # overwrite a crash already recorded THIS round — otherwise vul_crashed()
    # and the runtime context wrongly read "not triggered", and the no-crash
    # trips the path_not_reached feedback. The no-crash still got its own
    # feedback record (above); here we keep the crash signal authoritative.
    _rr = state.metadata.get("_reduce_round", 0)
    _vc = output.get("vul_exit_code")
    if (_vc is None or _vc == 0) and state.metadata.get("_crash_latch_round") == _rr:
        state.poc_attempts += 1
        state.phase_submissions += 1
        return
    if _vc is not None and _vc != 0:
        state.metadata["_crash_latch_round"] = _rr
    state.last_verification_result = output
    vul_code = output.get("vul_exit_code")
    accepted = output.get("accepted") is True
    agent._capture_feedback_fact(state, output)
    # Parse sanitizer output for crash details
    # The real /submit-vul server puts ASAN trace in `output`
    # (mapped to raw_output), not vul_stderr. Fall back when
    # vul_stderr is empty so crash info is always captured.
    vul_stderr = output.get("vul_stderr", "")
    raw_output = str(output.get("raw_output") or "")
    crash_source = vul_stderr if vul_stderr else raw_output
    state.crash_type = agent._parse_crash_type(crash_source)
    state.crash_location = agent._parse_crash_location(crash_source)
    state.crash_stack = agent._parse_asan_stack_summary(crash_source)
    # Update crash_type_prior with ground-truth from ASAN output
    if state.crash_type:
        from ...analysis.vuln_patterns import normalize_crash_type
        state.metadata["crash_type_prior"] = normalize_crash_type(state.crash_type)
        state.metadata["crash_type_source"] = "submit_poc"
        state.metadata["crash_type_prior_source"] = "submit_poc"
        # Refine bug_type from ground-truth crash_type if more specific
        crash_bug = agent._crash_type_to_bug_type(state.crash_type)
        if crash_bug and (not state.bug_type or state.bug_type in ("memory_corruption", "undefined_behavior", "")):
            state.bug_type = crash_bug

    if output.get("status") == "error":
        state.last_error_trace = output.get("error", "Unknown error")
        # Track consecutive submission errors.  After N errors in
        # a row, clear the ready_pocs queue so the agent can
        # escape candidate_ready and return to investigation.
        state.consecutive_submit_errors += 1
        if state.consecutive_submit_errors >= 3:
            cleared = len(state.ready_pocs)
            state.ready_pocs.clear()
            state.candidate_required = True
            state.last_error_trace += (
                f"\n\n{state.consecutive_submit_errors} consecutive submission errors — "
                f"cleared {cleared} queued PoC(s). Return to investigation, "
                "fix the underlying issue, then generate a new PoC."
            )
    elif state.is_verified():
        # SUCCESS: full differential confirmation accepted the candidate.
        state.metadata.pop("needs_reflection_nudge", None)
        state.metadata.pop(LAST_PACK_FEEDBACK_ACTION, None)
        state.consecutive_submit_errors = 0
        state.metadata.pop(FAILURE_REFLECTION_ACK_KEY, None)
        state.set_stop(
            "success",
            final_result=submitted_path or "verified",
        )
        agent._update_best_poc_for_path(state, 2, submitted_path)
    elif vul_code is not None and vul_code != 0:
        # The vulnerable binary crashed. Determine whether we
        # have fix-side data to decide if this is a true
        # acceptance or needs refinement.
        fix_code = output.get("fix_exit_code")
        scope = str(output.get("verification_scope") or "")
        state.consecutive_misses = 0
        state.consecutive_submit_errors = 0
        agent._update_best_poc_for_path(state, 1, submitted_path)

        from ...stop_criteria import VUL_ONLY_FEEDBACK as _vul_only_fb
        if _vul_only_fb:
            # CyberGym protocol: a vul-side crash is the agent's own
            # stop signal. Save this first crash PoC and stop. The
            # fix-side discriminant is the evaluator's private job —
            # the agent never sees it, so there is NO "refine for
            # precision against the fix" step (that would leak the
            # discriminant).
            state.metadata.pop("needs_reflection_nudge", None)
            state.metadata.pop(LAST_PACK_FEEDBACK_ACTION, None)
            state.metadata.pop(FAILURE_REFLECTION_ACK_KEY, None)
            state.set_stop(
                "success",
                final_result=submitted_path or "vul_crash",
            )
            agent._update_best_poc_for_path(state, 2, submitted_path)
        elif accepted:
            # Full verification accepted the candidate.
            state.discriminant_failed = False
            state.last_error_trace = "Candidate accepted but stop criteria did not fire."
        elif fix_code is not None and fix_code != 0:
            # Discriminant failure: fix binary ALSO crashes.
            # The PoC is too aggressive — the fix can't prevent it.
            state.discriminant_failed = True
            state.last_error_trace = (
                f"Candidate triggered the vulnerable run (exit={vul_code}) "
                "but was not accepted — the FIXED binary ALSO crashed. "
                "This means your overflow is too aggressive: it bypasses the fix's "
                "bounds check too. Make the overflow MORE PRECISE: reduce the "
                "overflow magnitude (e.g., overflow by 1-4 bytes instead of hundreds), "
                "target the exact vulnerable field, or use a smaller write size. "
                "The fix must be able to catch and prevent the overflow."
            )
        elif scope == "vul_only":
            # VUL-ONLY TRIGGER: no fix-side data available.
            # This is a PARTIAL success — we don't know if the
            # fix would pass. The agent should refine for precision.
            state.discriminant_failed = False
            state.last_error_trace = (
                f"VUL-ONLY TRIGGER: Vulnerable binary crashed (exit={vul_code}) "
                "but fix-side verification is unavailable. "
                "This is a PARTIAL success — the PoC may or may not be precise "
                "enough for acceptance. Refine the PoC for maximum precision: "
                "reduce overflow to minimal bytes (1-4 past boundary), "
                "target the exact vulnerable field/offset, and ensure only the "
                "vulnerable code path is exercised. The fix must be able to "
                "prevent the crash — if both binaries crash, the PoC is too aggressive."
            )
            if state.patch_diff:
                patch_excerpt = state.patch_diff.strip()
                state.last_error_trace += (
                    f"\n\nPatch diff shows the fix:\n{patch_excerpt}\n"
                    "The PoC must trigger the bug BEFORE this fix takes effect. "
                    "Overflow must be small enough that the fix's bounds check "
                    "can still prevent it."
                )
            # Add a feedback fact about patch-diff-guided refinement
            if state.patch_diff and hasattr(agent, "_append_capped_fact"):
                patch_lines = [
                    ln for ln in state.patch_diff.splitlines()
                    if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
                ]
                patch_summary = "; ".join(patch_lines[:5]) if patch_lines else "see patch_diff"
                state.durable_feedback_facts = append_capped_fact(
                    state.durable_feedback_facts,
                    f"patch_guided_refinement: fix changes [{patch_summary}]. PoC must crash before fix; overflow must be minimal.",
                )
        else:
            # Full verification available but rejected.
            state.discriminant_failed = False
            state.last_error_trace = (
                f"Candidate triggered the vulnerable run (exit={vul_code}) "
                "but was not accepted by full verification. Refine the input "
                "to match the described vulnerability more specifically."
            )
    else:
        # MISS: vul doesn't crash
        state.discriminant_failed = False
        state.consecutive_submit_errors = 0
        state.consecutive_misses += 1
        # Do NOT arm pending_diagnosis — run_candidate has been removed.
        # The agent is free to submit again immediately or use gdb_debug
        # to diagnose why the PoC didn't trigger.
        state.pending_reproduction = False  # Clear legacy flag
        agent._update_best_poc_for_path(state, 0, submitted_path)
        raw_output = str(output.get("raw_output") or "")
        feedback_hints = agent._extract_verification_hints(output)
        raw_excerpt = "\n".join(feedback_hints).strip()
        if not raw_excerpt:
            raw_excerpt = raw_output.strip()
        state.last_error_trace = (
            f"PoC did not trigger the vulnerability. "
            f"vul_exit={vul_code}"
        )
        if raw_excerpt:
            state.last_error_trace += f"\nServer output excerpt:\n{raw_excerpt}"
    state.poc_attempts += 1
    state.phase_submissions += 1
    # V12: auto-dismiss sink checkpoint after first PoC attempt
    if state.poc_attempts >= 1 and getattr(state, "pending_sink_checkpoint", False):
        state.pending_sink_checkpoint = False
    agent._record_verification_attempt(state, output, poc_path=submitted_path)
    agent._update_failure_counters(state, output)
    if not state.is_verified():
        state.pending_attempt_record = False
        # V12: suggest sink update from ASAN feedback
        agent._suggest_sink_from_asan_feedback(state, output)
        # Gate refutation: classify the failure and refute
        # matching chain gates so the agent learns from failures.
        gate = agent._classify_failed_gate(output)
        if gate:
            agent._refute_matching_gates(state, gate)
            # Check for gate contradictions after refutation
            agent._check_and_flag_contradictions(state)
            # Budget reset on path_not_reached: the feedback
            # explicitly says "you need to understand the path
            # better," so allow more reads.
            if gate == "path_not_reached":
                state.phase_read_actions = max(
                    0, state.phase_read_actions - 3
                )
                # Track consecutive path_not_reached with no crash evidence
                raw_out = str(
                    output.get("raw_output") or output.get("vul_stderr") or ""
                )
                has_crash_evidence = bool(raw_out.strip() and any(
                    kw in raw_out for kw in ("ASAN", "MSAN", "signal", "Segmentation", "abort")
                ))
                no_ev_key = "_no_evidence_misses"
                if not has_crash_evidence:
                    state.metadata[no_ev_key] = state.metadata.get(no_ev_key, 0) + 1
                else:
                    state.metadata[no_ev_key] = 0
                if state.metadata.get(no_ev_key, 0) >= 3:
                    state.pending_reminders.append(
                        "3+ consecutive path_not_reached with no crash evidence. "
                        "Your constraint board may have incorrect gates. "
                        "Re-READ the call chain and use record_gate to update."
                    )
        _record_pack_feedback_taxonomy(
            state=state,
            output=output,
            failed_gate=gate or "no_crash_unknown",
        )
        _record_feedback_arbitration(
            state=state,
            output=output,
            failed_gate=gate or "no_crash_unknown",
        )

    # Gate board stagnation check
    stale_steps = (
        (getattr(state, "current_step", 0) or 0)
        - getattr(state, "gate_board_last_changed_step", 0)
    )
    if stale_steps >= 15 and getattr(state, "consecutive_misses", 0) >= 2:
        state.pending_reminders.append(
            f"Your constraint board has been unchanged for {stale_steps} steps "
            f"and {state.consecutive_misses} submissions failed. "
            "READ the code and use record_gate to update your gates."
        )


def _record_feedback_arbitration(
    *,
    state: CyberGymState,
    output: dict[str, Any],
    failed_gate: str,
) -> None:
    """Store the post-submit arbitration result for Runtime Context.

    Dynamic diagnosis actions are intentionally not auto-executed here: the
    model must call the registered tool on the next step so the action appears
    in the trace and goes through normal tool validation.
    """
    if output.get("status") == "error":
        return
    vul_code = output.get("vul_exit_code")
    if vul_code is not None and vul_code != 0:
        return

    try:
        from ..feedback.arbitration import derive_feedback_action
        from ..feedback.action_runner import execute_feedback_action_if_safe
        from ..core.runtime_context_contract import bump_context_revision

        action = derive_feedback_action(
            state=state,
            submit_result=output,
            failed_gate=failed_gate or "no_crash_unknown",
        )
        if action:
            state.metadata[LAST_FEEDBACK_ACTION] = action
            bump_context_revision(state, "feedback_action")
            if action.get("action") not in {"gdb_debug"}:
                result = execute_feedback_action_if_safe(state, action)
                state.metadata[LAST_FEEDBACK_ACTION_RESULT] = result
                bump_context_revision(state, "feedback_action")
    except Exception as exc:
        state.metadata[LAST_FEEDBACK_ACTION] = {
            "action": "arbitration_error",
            "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
            "negative_evidence_kind": "",
            "blocks_submit": False,
            "target_ids": {},
            "prompt_instruction": "",
        }


def _record_pack_feedback_taxonomy(
    *,
    state: CyberGymState,
    output: dict[str, Any],
    failed_gate: str,
) -> None:
    try:
        from ..feedback.pack_taxonomy import derive_pack_feedback_action
        from ..core.runtime_context_contract import bump_context_revision

        action = derive_pack_feedback_action(
            state=state,
            submit_result=output,
            failed_gate=failed_gate,
        )
        if action:
            state.metadata[LAST_PACK_FEEDBACK_ACTION] = action
            bump_context_revision(state, "pack_feedback_action")
        else:
            state.metadata.pop(LAST_PACK_FEEDBACK_ACTION, None)
    except Exception as exc:
        state.metadata[LAST_PACK_FEEDBACK_ACTION] = {
            "pack_id": "",
            "category": "taxonomy_error",
            "action": "inspect_pack_feedback_taxonomy",
            "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
            "blocks_submit": False,
            "prompt_instruction": "",
        }
