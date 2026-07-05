"""Feedback effect — structured negative evidence generation and verification observation.

Extracted from FeedbackMixin to reduce mixin.py size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ...state import CyberGymState


def generate_feedback_effect(
    state: CyberGymState,
    gate: str,
    result: Dict[str, Any],
    submit_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate a structured feedback_effect dict from a failed submit.

    Returns dict with:
      - outcome: no_crash | wrong_crash | carrier_error | timeout | submission_error
      - likely_failure_layer: trigger | reachability | carrier | discriminant | unknown
      - recommended_revision: one-line action
      - affected_ids: dict of candidate_id, family_id, ranked_path_id, mapping_id
    """
    candidate_id = str(submit_context.get("candidate_id") or "")
    family_id = str(submit_context.get("family_id") or "")
    recipe = (state.metadata or {}).get("poc_recipe", {}) if isinstance(state.metadata, dict) else {}
    ranked_path_id = recipe.get("ranked_path_id", "") if isinstance(recipe, dict) else ""

    # Map gate -> (outcome, likely_failure_layer, recommended_revision)
    _GATE_MAP: Dict[str, tuple[str, str, str]] = {
        "carrier_parse": (
            "carrier_error",
            "carrier",
            "Fix carrier format — check magic bytes, headers, table directory",
        ),
        "no_crash_unknown": (
            "no_crash",
            "unknown",
            "Classify: reachability miss vs trigger-condition miss before replanning",
        ),
        "path_not_reached": (
            "no_crash",
            "reachability",
            "Check whether target is reachable from harness entry point",
        ),
        "path_reached_no_trigger": (
            "no_crash",
            "trigger",
            "Path reached but trigger condition unsatisfied — revise mutation value/offset",
        ),
        "trigger_condition_not_satisfied": (
            "no_crash",
            "trigger",
            "Trigger condition not met — adjust field value or size at mutation target",
        ),
        "malformed_substructure": (
            "carrier_error",
            "carrier",
            "Carrier parsed but target substructure malformed — fix field sizes/layout",
        ),
        "trigger_wrong_signature": (
            "wrong_crash",
            "trigger",
            "ASAN memory corruption detected but wrong crash type — refine overflow params",
        ),
        "trigger_wrong_location": (
            "wrong_crash",
            "reachability",
            "Crash in unexpected location — fix dispatch/routing field in PoC",
        ),
        "wrong_trigger": (
            "wrong_crash",
            "trigger",
            "Input reached parser but trigger condition wrong — change trigger bytes",
        ),
        "timeout_not_crash": (
            "no_crash",
            "reachability",
            "Execution timed out — simplify PoC for shorter path to vulnerability",
        ),
        "duplicate_candidate": (
            "submission_error",
            "carrier",
            "PoC was already submitted — modify content before resubmitting",
        ),
        "discriminant_failed": (
            "wrong_crash",
            "discriminant",
            "Overflow too broad — reduce to 1-4 bytes past boundary",
        ),
        "vul_only_triggered": (
            "wrong_crash",
            "discriminant",
            "Vul-only trigger — refine for precision with minimal overflow",
        ),
        "format_error": (
            "carrier_error",
            "carrier",
            "Carrier format invalid — fix structure before retry",
        ),
        "carrier_sanity_fail": (
            "carrier_error",
            "carrier",
            "Carrier sanity check failed — fix carrier structure (magic, tables, headers)",
        ),
    }

    outcome, layer, revision = _GATE_MAP.get(gate, ("no_crash", "unknown", "Review and replan"))

    # Escalation: check for repeated same-family no-trigger evidence
    ne_list: List[Dict[str, Any]] = (state.metadata or {}).get("negative_evidence", []) if isinstance(state.metadata, dict) else []
    if family_id:
        same_family_no_trigger = [
            r for r in ne_list
            if r.get("family_id") == family_id
            and r.get("kind") in ("path_reached_no_trigger", "no_crash_unknown")
            and r.get("ttl", 0) > 0
        ]
        if len(same_family_no_trigger) >= 2:
            revision = "Repeated no-trigger for same family — replan mutation strategy or rotate to different candidate"

    # UAF/uninit missing pair escalation (independent of family_id)
    from ...analysis.vulnerability_knowledge import crash_family
    vuln_family = crash_family(state.crash_type or state.bug_type or "")
    if vuln_family in ("lifetime", "uninitialized") and layer in ("trigger", "unknown"):
        recipe_mutations = recipe.get("trigger_mutations", []) if isinstance(recipe, dict) else []
        if not recipe_mutations:
            revision = f"{vuln_family} vulnerability but no paired endpoint — find_paired_endpoint before retrying"

    return {
        "outcome": outcome,
        "likely_failure_layer": layer,
        "recommended_revision": revision,
        "affected_ids": {
            "candidate_id": candidate_id,
            "family_id": family_id,
            "ranked_path_id": ranked_path_id,
        },
    }


def append_negative_evidence_from_feedback(
    state: CyberGymState,
    gate: str,
    feedback_effect: Dict[str, Any],
) -> str | None:
    """Append a negative evidence record from feedback if warranted.

    Returns evidence_id or None if no evidence was appended.
    """
    affected = feedback_effect.get("affected_ids", {})
    family_id = str(affected.get("family_id") or "")
    candidate_id = str(affected.get("candidate_id") or "")
    ranked_path_id = str(affected.get("ranked_path_id") or "")
    outcome = feedback_effect.get("outcome", "")

    # Map gate -> negative evidence kind
    _KIND_MAP: Dict[str, str] = {
        "no_crash_unknown": "no_crash_unknown",
        "path_not_reached": "path_not_reached",
        "trigger_wrong_signature": "wrong_crash",
        "trigger_wrong_location": "wrong_crash",
        "wrong_trigger": "path_reached_no_trigger",
        "timeout_not_crash": "no_crash_unknown",
        "carrier_parse": "format_error",
        "malformed_substructure": "format_error",
        "carrier_sanity_fail": "carrier_sanity_fail",
        "discriminant_failed": "wrong_crash",
        "vul_only_triggered": "wrong_crash",
        "duplicate_candidate": "repeated_candidate",
    }
    kind = _KIND_MAP.get(gate)
    if not kind:
        return None

    # Derive avoid_next directive
    avoid_next = ""
    layer = feedback_effect.get("likely_failure_layer", "")
    if layer == "carrier":
        avoid_next = "same_carrier_format"
    elif layer == "reachability":
        avoid_next = "same_path_without_routing_fix"
    elif layer == "trigger":
        avoid_next = "same_mutation_without_value_change"
    elif layer == "discriminant":
        avoid_next = "same_overflow_magnitude"

    summary = feedback_effect.get("recommended_revision", "") or gate

    evidence_id = state.append_negative_evidence(
        kind=kind,
        candidate_id=candidate_id,
        ranked_path_id=ranked_path_id,
        family_id=family_id,
        summary=summary,
        avoid_next=avoid_next,
    )

    # --- Consistency-scoped negative evidence ---
    from .consistency import append_consistency_negative_evidence
    append_consistency_negative_evidence(
        state, gate, candidate_id, ranked_path_id,
    )

    return evidence_id


def verification_observation_lines(
    agent: Any,
    state: CyberGymState,
) -> List[str]:
    """Return verification observation lines for display in prompts."""
    from ..core.constants import VUL_ONLY_FEEDBACK

    result = dict(state.last_verification_result or {})
    if VUL_ONLY_FEEDBACK:
        verdict = agent._agent_facing_verdict(result)
        lines = [f"- Result: `{verdict}` (vulnerable binary)"]
        # The real /submit-vul server puts ASAN trace in `output`
        # (mapped to raw_output), not vul_stderr. Fall back when empty.
        vul_stderr = str(result.get("vul_stderr", "") or "")
        raw_output = str(result.get("raw_output") or "")
        crash_source = vul_stderr if vul_stderr else raw_output
        crash = agent._parse_crash_type(crash_source)
        if crash:
            lines.append(f"- Crash type: {crash}")
        crash_loc = agent._parse_crash_location(crash_source) or getattr(state, "crash_location", "") or ""
        if crash_loc:
            lines.append(f"- Crash location: {crash_loc}")
        stack_summary = agent._parse_asan_stack_summary(crash_source)
        if stack_summary:
            lines.append(f"- Stack: {stack_summary}")
        if verdict not in ("crashed",):
            gate = agent._classify_failed_gate(result)
            if gate and gate != "duplicate_candidate":
                lines.append(f"- Failed gate: `{gate}`")
            hint = agent._failed_gate_repair_hint(gate)
            if hint:
                lines.append(f"- Repair hint: {hint}")
            from .gate_refutation import feedback_action_guidance
            action_hint = feedback_action_guidance(agent, state)
            if action_hint:
                lines.append(f"- {action_hint}")
        return lines
    lines = [f"- Verification: `{agent._verification_outcome_label(result)}`"]
    from .submit_records import extract_verification_hints
    hints = extract_verification_hints(result)
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
