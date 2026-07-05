"""Runtime context helpers for revision tracking and six-section rendering.

This module owns the model-visible context contract for structured runtime
state. The top-level observation sections stay stable; new content is injected
through named slots inside those sections.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .metadata_keys import (
    CONTEXT_REVISIONS,
    RUNTIME_EVIDENCE,
    bump_context_revision_value,
    get_context_revision_map,
    set_context_revision_map,
)

if TYPE_CHECKING:
    from ..state import CyberGymState

# ------------------------------------------------------------------
# Revision tracking
# ------------------------------------------------------------------

REVISION_KEYS: frozenset[str] = frozenset({
    "mechanism_graphs",
    "trigger_objectives",
    "input_mappings",
    "protocol_transcripts",
    "structured_rewrites",
    "consistency_signals",
    "local_mining_refs",
    "harness_protocols",
    "feedback_action",
    "poc_recipe",
    "numeric_constraints",
    "constraint_solutions",
    "domain_packs",
    "frontier_probes",
    "oracle_assessments",
    "dynamic_environment",
    "runtime_evidence",
    "candidate",
    "description",
    "harness",
    "mapping",
    "path",
})


def bump_context_revision(state: CyberGymState, key: str) -> None:
    """Bump a named revision counter so the next observation does a full refresh."""
    bump_context_revision_value(state, key, allowed=REVISION_KEYS)


def get_context_revisions(state: CyberGymState) -> Dict[str, int]:
    """Return the current revision map (may be empty)."""
    revisions = get_context_revision_map(state)
    if revisions and CONTEXT_REVISIONS not in state.metadata:
        set_context_revision_map(state, revisions)
    return revisions


def any_revision_changed(
    state: CyberGymState,
    baseline: Dict[str, int],
) -> bool:
    """Return True if any revision counter has changed since *baseline*."""
    current = get_context_revisions(state)
    for key in set(list(current.keys()) + list(baseline.keys())):
        if current.get(key, 0) != baseline.get(key, 0):
            return True
    return False


# ------------------------------------------------------------------
# Six-section rendering helpers
# ------------------------------------------------------------------

def render_assessment_contract_snippets(state: CyberGymState) -> List[str]:
    """Render objective/protocol/consistency summary into Current Assessment."""
    lines: List[str] = []

    # Active trigger objectives
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    active_objs = [o for o in objectives if o.get("status") == "active"]
    if active_objs:
        for obj in active_objs[:3]:
            oid = obj.get("objective_id", "")
            kind = obj.get("kind", "")
            func = obj.get("target_function", "")
            floc = obj.get("target_file", "")
            lline = obj.get("target_line", 0)
            loc = f" @{floc}:{lline}" if floc else ""
            observable = obj.get("observable", "")
            lines.append(
                f"- Active objective: {oid} kind={kind}{loc}"
                f"{' observable=' + observable if observable else ''}"
            )

    # Consistency blocks
    signals = list(getattr(state, "consistency_signals", []) or [])
    blocks = [s for s in signals if s.get("severity") == "block" or s.get("blocks_submit")]
    warns = [s for s in signals if s.get("severity") == "warn" and not s.get("blocks_submit")]
    if blocks:
        for sig in blocks[:3]:
            sid = sig.get("signal_id", "")
            kind = sig.get("kind", "")
            summary = sig.get("summary", "")
            repair = sig.get("repair_action", "")
            lines.append(
                f"- Consistency BLOCK {sid}: {kind}: {summary[:120]}"
            )
            if repair:
                lines.append(f"  repair: {repair[:120]}")
    if warns:
        for sig in warns[:2]:
            sid = sig.get("signal_id", "")
            kind = sig.get("kind", "")
            summary = sig.get("summary", "")
            lines.append(f"- Consistency WARN {sid}: {kind}: {summary[:120]}")

    # Harness contract summary (from consumption model)
    fmt = getattr(state, "input_format", None)
    if fmt:
        consumption = getattr(fmt, "consumption", None)
        if consumption:
            scope = str(getattr(consumption, "endpoint_scope", "") or "")
            carrier_stack = list(getattr(consumption, "carrier_stack", []) or [])
            arch = str(getattr(consumption, "architecture_selector", "") or "")
            tr_req = getattr(consumption, "transcript_required", False)
            if scope or carrier_stack or tr_req:
                parts = [f"endpoint_scope={scope}"] if scope else []
                if carrier_stack:
                    parts.append(f"carrier_stack=[{', '.join(carrier_stack)}]")
                if arch and arch != "unknown":
                    parts.append(f"arch={arch}")
                if tr_req:
                    parts.append("transcript_required=True")
                lines.append(f"- Harness contract: {', '.join(parts)}")

    # Harness protocol summary
    protocols = list(getattr(state, "harness_protocols", []) or [])
    if protocols:
        for proto in protocols[:2]:
            pid = proto.get("protocol_id", "")
            contract = proto.get("input_contract", "")
            delimiters = proto.get("record_delimiters", [])
            lines.append(
                f"- Harness protocol: {pid} contract={contract}"
                + (f" delimiter={delimiters}" if delimiters else "")
            )

    # Local mining refs summary
    refs = list(getattr(state, "local_mining_refs", []) or [])
    if refs:
        ref_kinds = {}
        for r in refs:
            k = r.get("kind", "unknown")
            ref_kinds[k] = ref_kinds.get(k, 0) + 1
        parts = [f"{k}({c})" for k, c in sorted(ref_kinds.items())]
        lines.append(f"- Local mining: {', '.join(parts)} available")

    oracle_assessments = list((state.metadata or {}).get("oracle_assessments", []) or [])
    for assessment in oracle_assessments[:2]:
        oid = assessment.get("objective_id", "")
        oracle = assessment.get("oracle_kind", "")
        observable = assessment.get("observable_by_submit", True)
        action = assessment.get("recommended_action", "")
        lines.append(
            f"- Oracle assessment: obj={oid} oracle={oracle} observable_by_submit={observable}"
            + (f" action={action}" if action else "")
        )

    runtime_records = [
        item for item in list((state.metadata or {}).get(RUNTIME_EVIDENCE, []) or [])
        if isinstance(item, dict)
    ]
    for record in runtime_records[-2:]:
        conclusion = str(record.get("conclusion") or record.get("status") or "")
        digest = str(record.get("candidate_digest") or "")[:12]
        objective = str(record.get("objective_id") or "")
        evidence_ref = str(record.get("evidence_ref") or "")
        parts = [f"outcome={conclusion}"]
        if digest:
            parts.append(f"candidate={digest}")
        if objective:
            parts.append(f"objective={objective}")
        if evidence_ref:
            parts.append(f"evidence={evidence_ref}")
        lines.append("- Runtime evidence: " + " ".join(parts))

    frontier = _latest_frontier_probe(state)
    if frontier:
        lines.append(
            f"- Frontier probe: status={frontier.get('status', '')} frontier={frontier.get('frontier', '')}"
            f" action={frontier.get('recommended_action', '')}"
        )

    # Candidate/objective cooldown
    feedback_action = (state.metadata or {}).get("last_feedback_action") or {}
    if feedback_action.get("blocks_submit"):
        action = feedback_action.get("action", "")
        reason = feedback_action.get("reason", "")
        lines.append(f"- Feedback block: {action} — {reason[:120]}")

    return lines


def render_path_contract_snippets(state: CyberGymState) -> List[str]:
    """Render mechanism graph summaries into Vulnerability Path."""
    lines: List[str] = []
    graphs = list(getattr(state, "crash_mechanism_graphs", []) or [])

    for g in graphs[:3]:
        gid = g.get("graph_id", "")
        family = g.get("mechanism_family", "unknown")
        nodes = g.get("nodes", [])
        missing = g.get("missing_roles", [])
        summary = g.get("summary", "")

        # Build compact chain from nodes
        chain_parts: List[str] = []
        for n in nodes[:5]:
            role = n.get("role", "?")
            func = n.get("function", "")
            chain_parts.append(f"{role}:{func}" if func else role)

        chain_str = " -> ".join(chain_parts) if chain_parts else summary[:80]
        line = f"- mechanism {gid}: {chain_str}"
        if missing:
            line += f"; missing: {', '.join(missing[:3])}"
        lines.append(line)

    return lines


def render_condition_contract_snippets(state: CyberGymState) -> List[str]:
    """Render active objectives, input fields, transcript/rewrite requirements
    into Required Conditions."""
    lines: List[str] = []

    # Trigger objectives with required conditions
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    for obj in objectives[:3]:
        oid = obj.get("objective_id", "")
        kind = obj.get("kind", "")
        observable = obj.get("observable", "")
        required_conds = obj.get("required_conditions", [])
        input_fields = obj.get("input_fields", [])
        anti_goals = obj.get("anti_goals", [])
        oracle_kind = obj.get("oracle_kind", "")

        parts = [f"objective {oid} kind={kind}"]
        if oracle_kind:
            parts.append(f"oracle={oracle_kind}")
        if observable:
            parts.append(f"observable={observable}")
        lines.append("- " + " | ".join(parts))

        for cond in required_conds[:3]:
            lines.append(f"  must: {cond}")
        for field in input_fields[:3]:
            f_name = field.get("field", "")
            role = field.get("argument_role", "")
            strategy = field.get("value_strategy", "")
            status = field.get("status", "")
            parts = [f_name]
            if role:
                parts.append(f"role={role}")
            if strategy:
                parts.append(f"strategy={strategy}")
            if status:
                parts.append(f"status={status}")
            lines.append(f"  input field: {' '.join(parts)}")
        for ag in anti_goals[:2]:
            lines.append(f"  anti-goal: {ag}")

    # Input mappings with status
    mappings = list(getattr(state, "active_input_mappings", []) or [])
    unresolved = [m for m in mappings if m.get("status") == "needs_field_localization"]
    for m in unresolved[:3]:
        mid = m.get("mapping_id", "")
        role = m.get("argument_role", "")
        sink_expr = m.get("sink_expression", "")
        lines.append(
            f"- mapping {mid}: {role} {sink_expr} status=needs_field_localization"
        )

    # Protocol transcript plans
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    for tr in transcripts[:2]:
        tid = tr.get("transcript_id", "")
        scope = tr.get("harness_endpoint_scope", "")
        steps = tr.get("steps", [])
        required_order = tr.get("required_order", [])
        step_summary = " -> ".join(
            s.get("role", "?") for s in steps[:5]
        ) if steps else "no steps"
        lines.append(
            f"- transcript {tid}: scope={scope} steps={step_summary}"
        )
        if required_order:
            lines.append(f"  order: {' -> '.join(required_order)}")

    # Structured rewrite plans
    rewrites = list(getattr(state, "structured_rewrite_plans", []) or [])
    for rw in rewrites[:2]:
        rid = rw.get("rewrite_id", "")
        fmt = rw.get("carrier_format", "")
        ops = rw.get("operations", [])
        invariants = rw.get("invariants", [])
        lines.append(
            f"- rewrite {rid}: carrier={fmt} operations={len(ops)}"
            + (f" invariants=[{', '.join(invariants[:3])}]" if invariants else "")
        )

    domain_packs = list((state.metadata or {}).get("domain_packs", []) or [])
    for pack in domain_packs[:3]:
        lines.append(
            f"- domain pack {pack.get('pack', '')}: status={pack.get('status', '')} score={pack.get('match_score', '')}"
        )
        for gap in list(pack.get("open_gaps") or [])[:2]:
            lines.append(f"  pack_gap: {gap}")

    numeric_constraints = list((state.metadata or {}).get("numeric_constraints", []) or [])
    for constraint in numeric_constraints[:3]:
        lines.append(
            f"- numeric constraint {constraint.get('constraint_id', '')}: {constraint.get('kind', '')} {str(constraint.get('formula', ''))[:120]}"
        )

    constraint_solutions = list((state.metadata or {}).get("constraint_solutions", []) or [])
    for solution in constraint_solutions[:2]:
        lines.append(
            f"- constraint solution {solution.get('solution_id', '')}: status={solution.get('status', '')} assignments={len(solution.get('assignments', []) or [])}"
        )

    # Harness selectors / delimiters from harness_protocols
    protocols = list(getattr(state, "harness_protocols", []) or [])
    for proto in protocols[:2]:
        selectors = proto.get("selector_fields", [])
        delimiters = proto.get("record_delimiters", [])
        for sel in selectors[:2]:
            field = sel.get("field", "")
            meaning = sel.get("meaning", "")
            encoding = sel.get("encoding", "")
            lines.append(
                f"- harness selector: {field} meaning={meaning}"
                + (f" encoding={encoding}" if encoding else "")
            )
        for delim in delimiters[:2]:
            lines.append(f"- harness delimiter: {delim!r}")

    # PoC recipe summary
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()
    if recipe and recipe.get("recipe_id"):
        rid = recipe.get("recipe_id", "")
        carrier = recipe.get("carrier", {}) or {}
        carrier_format = carrier.get("format", "")
        seed = carrier.get("seed_path", "")
        scope = carrier.get("endpoint_scope", "")
        mutations = recipe.get("trigger_mutations", [])
        open_gaps = recipe.get("open_gaps", [])
        rewrite = recipe.get("rewrite", {}) or {}
        invariants = rewrite.get("invariants", [])

        parts = [f"recipe {rid}:"]
        if carrier_format:
            parts.append(f"carrier={carrier_format}")
        if seed:
            parts.append(f"seed={seed}")
        if scope:
            parts.append(f"scope={scope}")
        lines.append("- " + " ".join(parts))

        for mut in mutations[:3]:
            role = mut.get("argument_role", "")
            strategy = mut.get("value_strategy", "")
            executable = mut.get("executable", False)
            status = "READY" if executable else "NEEDS_OFFSET"
            lines.append(f"  mutation: {role} strategy={strategy} [{status}]")

        if invariants:
            lines.append(f"  invariants: {', '.join(str(i) for i in invariants[:3])}")

        if open_gaps:
            for gap in open_gaps[:3]:
                lines.append(f"  open_gap: {gap}")

    return lines


def render_experiment_contract_snippets(state: CyberGymState) -> List[str]:
    """Render negative evidence scoped to objective/transcript/rewrite/consistency
    into Experiments."""
    lines: List[str] = []
    ne_list: List[Dict[str, Any]] = (state.metadata or {}).get("negative_evidence", [])
    if not isinstance(ne_list, list):
        ne_list = []

    # Filter for context-scoped evidence
    scoped_kinds = {
        "objective_not_satisfied",
        "transcript_order_mismatch",
        "transcript_endpoint_mismatch",
        "structured_rewrite_invalid",
        "consistency_block",
        "wrong_harness_binary",
        "wrong_format_scope",
        "sanitizer_origin_missed",
        "objective_not_observable",
    }

    scoped_evidence = [e for e in ne_list if e.get("kind") in scoped_kinds and e.get("ttl", 0) > 0]
    for ev in scoped_evidence[-5:]:
        kind = ev.get("kind", "")
        summary = ev.get("summary", "")[:100]
        oid = ev.get("objective_id", "")
        tid = ev.get("transcript_id", "")
        rid = ev.get("rewrite_id", "")
        avoid = ev.get("avoid_next", "")

        scope_parts = []
        if oid:
            scope_parts.append(f"obj={oid}")
        if tid:
            scope_parts.append(f"tr={tid}")
        if rid:
            scope_parts.append(f"rw={rid}")
        scope_str = f" scoped={' '.join(scope_parts)}" if scope_parts else ""

        line = f"- [{kind}]{scope_str}: {summary}"
        if avoid:
            line += f" avoid: {avoid[:80]}"
        lines.append(line)

    # Last feedback action
    feedback_action = (state.metadata or {}).get("last_feedback_action") or {}
    if feedback_action:
        action = feedback_action.get("action", "")
        reason = feedback_action.get("reason", "")[:100]
        if action:
            blocks = feedback_action.get("blocks_submit", False)
            block_tag = " BLOCKED" if blocks else ""
            lines.append(f"- feedback{block_tag}: {action} — {reason}")

    # Last PoC sanity
    last_sanity = (state.metadata or {}).get("last_poc_sanity") or {}
    if last_sanity and not last_sanity.get("passed", True):
        issues = last_sanity.get("issues", [])
        for issue in issues[:2]:
            sev = issue.get("severity", "")
            kind = issue.get("kind", "")
            summary = issue.get("summary", "")[:80]
            lines.append(f"- sanity {sev}: [{kind}] {summary}")

    last_build = (state.metadata or {}).get("last_poc_build_result") or {}
    if last_build:
        status = last_build.get("status", "")
        rid = last_build.get("recipe_id", "")
        path = last_build.get("candidate_path", "")
        reason = last_build.get("reason", "")
        lines.append(f"- candidate build: status={status} recipe={rid}" + (f" path={path}" if path else ""))
        if reason:
            lines.append(f"  build_reason: {str(reason)[:100]}")

    frontier = _latest_frontier_probe(state)
    if frontier:
        lines.append(
            f"- frontier probe: status={frontier.get('status', '')} frontier={frontier.get('frontier', '')} action={frontier.get('recommended_action', '')}"
        )

    return lines


def derive_contract_next_action_block(state: CyberGymState) -> Dict[str, str]:
    """Return required action, reason, target id, and stop condition for Next Action.

    Priority order (higher = more important):
    1. Feedback arbitration hard block
    2. Sanity fail
    3. Consistency block
    4. Transcript gap
    5. Feedback arbitration soft action
    6. Objective missing required fields
    7. Recipe open gaps
    8. No active objective but ranked path exists
    """
    metadata = getattr(state, "metadata", {}) or {}

    # 1. Feedback arbitration hard block
    feedback_action = metadata.get("last_feedback_action") or {}
    if feedback_action.get("blocks_submit"):
        action = feedback_action.get("action", "")
        reason = feedback_action.get("reason", "")
        target_ids = feedback_action.get("target_ids", {}) or {}
        stop_cond = feedback_action.get("prompt_instruction", "")
        return {
            "required": f"Feedback required: {action}",
            "why": reason[:200],
            "target": "; ".join(f"{k}={v}" for k, v in target_ids.items() if v) or "",
            "stop_condition": stop_cond[:200] if stop_cond else "complete the required action before submit",
            "do_not": "submit another PoC until this action is completed",
        }

    # 2. Sanity fail
    last_sanity = metadata.get("last_poc_sanity") or {}
    if last_sanity and not last_sanity.get("passed", True):
        issues = last_sanity.get("issues", [])
        repair = ""
        for issue in issues:
            if issue.get("repair_hint"):
                repair = issue["repair_hint"]
                break
        return {
            "required": "Fix carrier sanity failure",
            "why": f"PoC failed sanity: {issues[0].get('summary', '')[:120]}" if issues else "PoC carrier is invalid",
            "target": repair[:200] if repair else "fix magic/header/container structure",
            "stop_condition": "sanity check passes before submit",
            "do_not": "submit this PoC until carrier structure is valid",
        }

    # 3. Consistency block
    signals = list(getattr(state, "consistency_signals", []) or [])
    blocks = [s for s in signals if s.get("severity") == "block" or s.get("blocks_submit")]
    if blocks:
        sig = blocks[0]
        return {
            "required": f"Repair consistency: {sig.get('kind', 'unknown')}",
            "why": sig.get("summary", "")[:200],
            "target": sig.get("repair_action", "")[:200],
            "stop_condition": "consistency block is cleared",
            "do_not": "submit until consistency block is resolved",
        }

    # 3b. Oracle/frontier hard context before transcript/candidate readiness.
    for obj in list(getattr(state, "active_trigger_objectives", []) or []):
        if obj.get("status") == "active" and (
            obj.get("observable_by_submit") is False
            or obj.get("no_trigger_diagnosis") == "oracle_not_observable"
        ):
            return {
                "required": "verify_oracle_context",
                "why": obj.get("observability_reason", "")[:200] or "active objective is not observable by submit_poc",
                "target": obj.get("objective_id", ""),
                "stop_condition": "objective is marked observable_by_submit=true or a new objective is selected",
                "do_not": "submit more crash variants for an unobservable objective",
            }

    frontier = _latest_frontier_probe(state)
    if frontier and _frontier_requires_action(str(frontier.get("status") or "")):
        return {
            "required": str(frontier.get("recommended_action") or "extract_harness_protocol"),
            "why": str(frontier.get("reason") or "")[:200],
            "target": f"frontier={frontier.get('frontier', '')} status={frontier.get('status', '')}",
            "stop_condition": "latest frontier status is resolved or a newer probe changes the diagnosis",
            "do_not": "submit unchanged PoC before resolving the frontier",
        }

    # 4. Transcript gap (also checks recipe coverage)
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    active_tr = [t for t in transcripts if t.get("status") == "active"]
    if active_tr:
        tr = active_tr[0]
        steps = tr.get("steps", [])
        if not steps or len(steps) < 2:
            return {
                "required": f"Complete transcript {tr.get('transcript_id', '')}",
                "why": "transcript requires ordered steps but plan is incomplete",
                "target": tr.get("transcript_id", ""),
                "stop_condition": "transcript has all required steps in order",
                "do_not": "submit single-buffer PoC; this target needs an ordered transcript",
            }
        # Check recipe coverage via transcript_runtime
        try:
            from .transcript_runtime import transcript_gap_for_current_recipe
            gap = transcript_gap_for_current_recipe(state)
            if gap.get("wrong_scope") or gap.get("missing_steps"):
                tid = tr.get("transcript_id", "")
                summary = gap.get("summary", "")[:200]
                return {
                    "required": f"Complete transcript {tid} coverage",
                    "why": summary or "recipe does not cover all transcript steps",
                    "target": ", ".join(gap.get("missing_steps", [])),
                    "stop_condition": "recipe covers all transcript steps in order",
                    "do_not": "submit single-buffer PoC; complete transcript first",
                }
        except Exception:
            pass

    # 5. Feedback arbitration soft action
    if feedback_action and feedback_action.get("action") and not feedback_action.get("blocks_submit"):
        action = feedback_action["action"]
        if action in ("localize_field", "switch_objective", "mine_local_tests",
                       "extract_harness_protocol", "change_seed", "repair_carrier",
                       "verify_oracle_context"):
            return {
                "required": f"{action}",
                "why": feedback_action.get("reason", "")[:200],
                "target": "; ".join(f"{k}={v}" for k, v in (feedback_action.get("target_ids", {}) or {}).items() if v),
                "stop_condition": feedback_action.get("prompt_instruction", "")[:200],
                "do_not": "",
            }

    # 6. Objective missing required fields
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    active_objs = [o for o in objectives if o.get("status") == "active"]
    for obj in active_objs:
        input_fields = obj.get("input_fields", [])
        has_unresolved = any(
            f.get("status") == "needs_field_localization" for f in input_fields
        )
        if has_unresolved:
            oid = obj.get("objective_id", "")
            unresolved = [f for f in input_fields if f.get("status") == "needs_field_localization"]
            return {
                "required": f"Localize field for objective {oid}",
                "why": f"objective has {len(unresolved)} unresolved input field(s)",
                "target": ", ".join(f.get('field', '?') for f in unresolved[:3]),
                "stop_condition": "all input fields have resolved offset+width",
                "do_not": "submit PoC for this objective until fields are localized",
            }

    # 7. Recipe open gaps
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()
    open_gaps = recipe.get("open_gaps", [])
    if open_gaps:
        return {
            "required": f"Resolve recipe gap: {open_gaps[0]}",
            "why": f"recipe has {len(open_gaps)} open gap(s)",
            "target": open_gaps[0],
            "stop_condition": "recipe open_gaps is empty",
            "do_not": "submit PoC until recipe gaps are resolved",
        }

    # 8. No active objective but ranked path exists (only in investigation+)
    ranked_paths = list(getattr(state, "ranked_vulnerability_paths", []) or [])
    phase = str(getattr(state, "current_phase", "") or "")
    if ranked_paths and not active_objs and phase in ("investigation", "formulation", "verification"):
        return {
            "required": "Create trigger objective for ranked path",
            "why": "ranked path exists but no active objective",
            "target": ranked_paths[0].get("path_id", ""),
            "stop_condition": "at least one active trigger objective exists",
            "do_not": "",
        }

    # No context blocker — let the default Next Action logic proceed
    return {}


def _latest_frontier_probe(state: CyberGymState) -> Dict[str, Any]:
    probes = (state.metadata or {}).get("frontier_probes", [])
    if isinstance(probes, list) and probes:
        latest = probes[-1]
        return latest if isinstance(latest, dict) else {}
    return {}


def _frontier_requires_action(status: str) -> bool:
    normalized = {
        "harness_not_reached": "path_not_reached",
        "parser_rejected": "path_not_reached",
        "dispatch_not_selected": "path_not_reached",
        "sink_not_reached": "path_not_reached",
        "sink_reached_trigger_unmet": "trigger_unmet",
        "capability_error": "frontier_unknown",
        "inconclusive": "frontier_unknown",
    }.get(status, status)
    return normalized in {
        "wrong_harness",
        "path_not_reached",
        "trigger_unmet",
        "oracle_not_observable",
        "frontier_unknown",
    }


# ------------------------------------------------------------------
# Hard contract slots — Fix A
# ------------------------------------------------------------------

def render_context_contract_slots(state: CyberGymState) -> Dict[str, List[str]]:
    """Render the mandatory context slots for each observation section.

    These slots MUST appear at fixed positions in the six-section
    observation, regardless of how much legacy content also exists.
    The observation renderer places these before legacy content in
    each section.

    Returns a dict keyed by section name, each value a list of lines
    that must appear in that section's output.
    """
    slots: Dict[str, List[str]] = {
        "assessment": [],
        "vuln_path": [],
        "conditions": [],
        "experiments": [],
        "next_action": [],
    }

    # === Assessment slots (must appear in first 8 lines) ===

    # 1. Active objective
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    active_objs = [o for o in objectives if o.get("status") == "active"]
    if active_objs:
        for obj in active_objs[:2]:
            oid = obj.get("objective_id", "")
            kind = obj.get("kind", "")
            oracle_kind = obj.get("oracle_kind", "")
            oracle_signal = obj.get("oracle_signal", "")
            no_trigger_diag = obj.get("no_trigger_diagnosis", "")
            func = obj.get("target_function", "")
            loc = ""
            if obj.get("target_file"):
                loc = f" @{obj['target_file']}:{obj.get('target_line', 0)}"
            parts = [f"Active objective: {oid} kind={kind}"]
            if oracle_kind:
                parts.append(f"oracle={oracle_kind}")
            if oracle_signal:
                parts.append(f"signal={oracle_signal}")
            if func:
                parts.append(f"target={func}{loc}")
            slots["assessment"].append("- " + " | ".join(parts))
            # No-trigger diagnosis (Fix F)
            if no_trigger_diag:
                diag_label = no_trigger_diag.replace("_", " ")
                slots["assessment"].append(
                    f"  No-trigger diagnosis: {diag_label}"
                )
            if obj.get("observable_by_submit") is False:
                slots["assessment"].append(
                    f"  Oracle observability: false — {str(obj.get('observability_reason', ''))[:100]}"
                )
            # Missing fields summary
            input_fields = obj.get("input_fields", [])
            unresolved = [f for f in input_fields if f.get("status") == "needs_field_localization"]
            if unresolved:
                slots["assessment"].append(
                    f"  missing fields: {', '.join(f.get('field', '?') for f in unresolved[:3])}"
                )
    else:
        slots["assessment"].append("- Active objective: (none)")

    # 2. Consistency status
    signals = list(getattr(state, "consistency_signals", []) or [])
    blocks = [s for s in signals if s.get("severity") == "block" or s.get("blocks_submit")]
    if blocks:
        for sig in blocks[:2]:
            sid = sig.get("signal_id", "")
            kind = sig.get("kind", "")
            summary = sig.get("summary", "")[:100]
            slots["assessment"].append(f"- Consistency BLOCK {sid}: {kind}: {summary}")
            repair = sig.get("repair_action", "")
            if repair:
                slots["assessment"].append(f"  repair: {repair[:100]}")
    else:
        warns = [s for s in signals if s.get("severity") == "warn"]
        if warns:
            for sig in warns[:1]:
                sid = sig.get("signal_id", "")
                kind = sig.get("kind", "")
                slots["assessment"].append(f"- Consistency WARN {sid}: {kind}")
        else:
            slots["assessment"].append("- Consistency status: PASS")

    frontier = _latest_frontier_probe(state)
    if frontier:
        slots["assessment"].append(
            f"- Frontier: status={frontier.get('status', '')} frontier={frontier.get('frontier', '')} action={frontier.get('recommended_action', '')}"
        )

    # === Vulnerability Path slots ===

    graphs = list(getattr(state, "crash_mechanism_graphs", []) or [])
    if graphs:
        for g in graphs[:2]:
            gid = g.get("graph_id", "")
            family = g.get("mechanism_family", "unknown")
            nodes = g.get("nodes", [])
            chain_parts = []
            for n in nodes[:5]:
                role = n.get("role", "?")
                func = n.get("function", "")
                chain_parts.append(f"{role}:{func}" if func else role)
            chain_str = " -> ".join(chain_parts) if chain_parts else "(empty)"
            missing = g.get("missing_roles", [])
            line = f"- Mechanism graph {gid}: [{family}] {chain_str}"
            if missing:
                line += f"; missing: {', '.join(missing[:3])}"
            slots["vuln_path"].append(line)
    else:
        slots["vuln_path"].append("- No mechanism graph yet")

    # === Conditions slots (prioritized: objective formula > input fields > recipe gaps > legacy) ===

    # 1. Trigger objective formulas
    for obj in active_objs[:2]:
        oid = obj.get("objective_id", "")
        kind = obj.get("kind", "")
        violation = obj.get("violation_formula", "")
        oracle_kind = obj.get("oracle_kind", "")
        no_trigger_diag = obj.get("no_trigger_diagnosis", "")
        parts = [f"Trigger objective {oid} kind={kind}"]
        if oracle_kind:
            parts.append(f"oracle={oracle_kind}")
        if violation:
            parts.append(f"formula={violation}")
        if no_trigger_diag:
            parts.append(f"no_trigger_diagnosis={no_trigger_diag}")
        slots["conditions"].append("- " + " | ".join(parts))

        input_fields = obj.get("input_fields", [])
        for field in input_fields[:4]:
            f_name = field.get("field", "")
            role = field.get("argument_role", "")
            status = field.get("status", "")
            parts = [f_name]
            if role:
                parts.append(f"role={role}")
            if status:
                parts.append(f"status={status}")
            slots["conditions"].append(f"  input field: {' '.join(parts)}")

    # 2. Recipe gaps
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()
    open_gaps = recipe.get("open_gaps", [])
    if open_gaps:
        slots["conditions"].append(f"- Recipe gaps ({len(open_gaps)}): {', '.join(str(g)[:60] for g in open_gaps[:4])}")

    domain_packs = list((state.metadata or {}).get("domain_packs", []) or [])
    for pack in domain_packs[:2]:
        slots["conditions"].append(
            f"- Domain pack {pack.get('pack', '')}: status={pack.get('status', '')}"
        )

    numeric_constraints = list((state.metadata or {}).get("numeric_constraints", []) or [])
    if numeric_constraints:
        slots["conditions"].append(
            f"- Numeric constraints: {len(numeric_constraints)} extracted; solve/localize before byte rewrite"
        )
    constraint_solutions = list((state.metadata or {}).get("constraint_solutions", []) or [])
    for solution in constraint_solutions[:2]:
        slots["conditions"].append(
            f"- Constraint solution {solution.get('solution_id', '')}: status={solution.get('status', '')} assignments={len(solution.get('assignments', []) or [])}"
        )

    # 3. Harness selectors / delimiters (from harness_protocols)
    protocols = list(getattr(state, "harness_protocols", []) or [])
    for proto in protocols[:2]:
        selectors = proto.get("selector_fields", [])
        delimiters = proto.get("record_delimiters", [])
        if selectors:
            for sel in selectors[:2]:
                field = sel.get("field", "")
                meaning = sel.get("meaning", "")
                slots["conditions"].append(f"- Harness selector: {field} meaning={meaning}")
        if delimiters:
            for delim in delimiters[:2]:
                slots["conditions"].append(f"- Harness delimiter: {delim!r}")

    # === Experiments slots ===

    # 1. Last feedback action (before attempt table)
    feedback_action = (state.metadata or {}).get("last_feedback_action") or {}
    if feedback_action:
        action = feedback_action.get("action", "")
        reason = feedback_action.get("reason", "")[:100]
        blocks = feedback_action.get("blocks_submit", False)
        if action:
            tag = " BLOCKED" if blocks else ""
            slots["experiments"].append(f"- Feedback{tag}: {action} — {reason}")

    # 2. Runtime evidence (compact only; full stdout/stderr stays in artifacts)
    runtime_records = [
        item for item in list((state.metadata or {}).get(RUNTIME_EVIDENCE, []) or [])
        if isinstance(item, dict)
    ]
    for record in runtime_records[-2:]:
        outcome = str(record.get("conclusion") or record.get("status") or "")
        candidate = str(record.get("candidate_digest") or "")[:12]
        evidence_ref = str(record.get("evidence_ref") or "")
        objective = str(record.get("objective_id") or "")
        parts = [f"outcome={outcome}"]
        if candidate:
            parts.append(f"candidate={candidate}")
        if objective:
            parts.append(f"obj={objective}")
        if evidence_ref:
            parts.append(f"evidence={evidence_ref}")
        slots["experiments"].append("- Runtime evidence: " + " ".join(parts))

    frontier = _latest_frontier_probe(state)
    if frontier:
        evidence_ref = str(frontier.get("evidence_ref") or "")
        suffix = f" evidence={evidence_ref}" if evidence_ref else ""
        slots["experiments"].append(
            f"- GDB frontier: status={frontier.get('status', '')} "
            f"last_hit={frontier.get('last_hit_role', '')} "
            f"first_unreached={frontier.get('first_unreached_role', '')} "
            f"action={frontier.get('recommended_action', '')}{suffix}"
        )

    # 3. Scoped negative evidence
    ne_list: List[Dict[str, Any]] = (state.metadata or {}).get("negative_evidence", [])
    if isinstance(ne_list, list):
        scoped_kinds = {
            "objective_not_satisfied", "transcript_order_mismatch",
            "transcript_endpoint_mismatch", "structured_rewrite_invalid",
            "consistency_block", "wrong_harness_binary", "wrong_format_scope",
            "sanitizer_origin_missed", "objective_not_observable",
            "path_not_reached", "path_reached_no_trigger",
            "trigger_condition_not_satisfied", "frontier_unknown",
        }
        scoped_ev = [e for e in ne_list if e.get("kind") in scoped_kinds and e.get("ttl", 0) > 0]
        for ev in scoped_ev[-3:]:
            kind = ev.get("kind", "")
            summary = ev.get("summary", "")[:80]
            oid = ev.get("objective_id", "")
            scope = f" obj={oid}" if oid else ""
            slots["experiments"].append(f"- [{kind}]{scope}: {summary}")

    # 4. Last PoC sanity
    last_sanity = (state.metadata or {}).get("last_poc_sanity") or {}
    if last_sanity and not last_sanity.get("passed", True):
        issues = last_sanity.get("issues", [])
        for issue in issues[:2]:
            sev = issue.get("severity", "")
            summary = issue.get("summary", "")[:80]
            slots["experiments"].append(f"- Sanity {sev}: {summary}")

    # 5. Feedback action runner result
    runner_result = (state.metadata or {}).get("last_feedback_action_result") or {}
    if runner_result:
        status = runner_result.get("status", "")
        name = runner_result.get("action", "")
        if status and name:
            slots["experiments"].append(f"- Action runner: {name} → {status}")

    last_build = (state.metadata or {}).get("last_poc_build_result") or {}
    if last_build:
        slots["experiments"].append(
            f"- Candidate builder: {last_build.get('status', '')} recipe={last_build.get('recipe_id', '')}"
        )

    # === Next Action slot (single required action from derive_contract_next_action_block) ===

    contract_block = derive_contract_next_action_block(state)
    if contract_block:
        required = contract_block.get("required", "")
        why = contract_block.get("why", "")
        target = contract_block.get("target", "")
        stop = contract_block.get("stop_condition", "")
        do_not = contract_block.get("do_not", "")
        slots["next_action"].append(f"**Required**: {required}")
        if why:
            slots["next_action"].append(f"- Why: {why}")
        if target:
            slots["next_action"].append(f"- Target: {target}")
        if stop:
            slots["next_action"].append(f"- Stop condition: {stop}")
        if do_not:
            slots["next_action"].append(f"- Do not: {do_not}")

    return slots
