"""Registered tool-result processors for CyberGymAgent.reduce().

Each handler receives (agent, state, result, output) and returns None.
Handlers mutate state directly, same as the original inline code.

The registry replaces the large if/elif chain in _process_action_result().
Submit_poc is handled by agent._process_submit_result() because it is deeply
coupled to agent mixin methods and would be fragile to extract.
"""

from __future__ import annotations

from typing import Any, Callable

from ...state import CyberGymState
from ..core.fact_extraction import extract_poc_paths_from_bash
from ..core.metadata_keys import FRONTIER_PROBES, RUNTIME_EVIDENCE


# Type alias for handler functions
HandlerFn = Callable[[Any, CyberGymState, Any, Any], None]


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, HandlerFn] = {}


def register_handler(tool_name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator to register a tool result handler."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        _HANDLERS[tool_name] = fn
        return fn
    return decorator


def get_handler(tool_name: str) -> HandlerFn | None:
    """Look up a registered handler by tool name (case-insensitive)."""
    return _HANDLERS.get(tool_name) or _HANDLERS.get(tool_name.lower())


# ---------------------------------------------------------------------------
# Submit PoC handler — delegates to agent method
# ---------------------------------------------------------------------------

@register_handler("submit_poc")
def handle_submit_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Delegate submit processing to the agent's own method.

    The submit flow is deeply coupled to agent mixin methods
    (_append_feedback_record, _capture_feedback_fact, _parse_crash_type,
    etc.), so it remains as an agent method rather than a standalone function.
    """
    agent._process_submit_result(state, result, output)


# ---------------------------------------------------------------------------
# Dynamic execution handler
# ---------------------------------------------------------------------------

@register_handler("run_candidate")
def handle_run_candidate_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Persist compact dynamic execution evidence."""
    if not isinstance(output, dict):
        return
    if output.get("status") != "success":
        return

    from ..core.runtime_context_contract import bump_context_revision

    evidence_list = state.metadata.setdefault(RUNTIME_EVIDENCE, [])
    if not isinstance(evidence_list, list):
        evidence_list = []
        state.metadata[RUNTIME_EVIDENCE] = evidence_list

    evidence_id = f"rte_{len(evidence_list):04d}"
    record = {
        "evidence_id": evidence_id,
        "source_kind": "candidate_run",
        "candidate_digest": output.get("candidate_digest", ""),
        "candidate_path": output.get("candidate_path", ""),
        "objective_id": output.get("objective_id", ""),
        "conclusion": output.get("outcome", ""),
        "status": output.get("outcome", ""),
        "confidence": 0.9 if output.get("outcome") in {"sanitizer_failure", "signal_failure", "clean_exit", "input_rejected"} else 0.6,
        "evidence_ref": output.get("evidence_ref", ""),
        "observed_at_step": int(getattr(state, "current_step", 0) or 0),
        "purpose": output.get("purpose", ""),
        "top_frame": output.get("top_frame", ""),
        "sanitizer_kind": output.get("sanitizer_kind", ""),
        "signal_name": output.get("signal_name", ""),
    }
    evidence_list.append(record)
    state.metadata[RUNTIME_EVIDENCE] = evidence_list[-12:]

    outcome = str(output.get("outcome") or "")
    if outcome in {"clean_exit", "input_rejected", "profile_unresolved", "environment_error", "timeout"}:
        kind = {
            "clean_exit": "path_reached_no_trigger",
            "input_rejected": "path_not_reached",
            "profile_unresolved": "wrong_harness_binary",
            "environment_error": "frontier_unknown",
            "timeout": "frontier_unknown",
        }.get(outcome, "frontier_unknown")
        try:
            state.append_negative_evidence(
                kind=kind,
                objective_id=str(output.get("objective_id") or ""),
                summary=f"run_candidate outcome={outcome}",
                avoid_next="Do not blindly resubmit the same candidate without changing carrier/path/trigger fields.",
            )
        except Exception:
            pass

    bump_context_revision(state, "runtime_evidence")


@register_handler("probe_runtime_frontier")
def handle_probe_runtime_frontier_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Persist compact GDB frontier probe evidence."""
    if not isinstance(output, dict):
        return
    if output.get("tool_status") not in {None, "", "success"}:
        return

    from ..core.runtime_context_contract import bump_context_revision

    probes = state.metadata.setdefault(FRONTIER_PROBES, [])
    if not isinstance(probes, list):
        probes = []
        state.metadata[FRONTIER_PROBES] = probes
    record = {
        "probe_id": f"gdb_frontier_{len(probes):04d}",
        "source_kind": "gdb_frontier",
        "candidate_digest": output.get("candidate_digest", ""),
        "candidate_path": output.get("candidate_path", ""),
        "objective_id": output.get("objective_id", ""),
        "path_id": output.get("path_id", ""),
        "status": output.get("status", ""),
        "frontier_status": output.get("status", ""),
        "runtime_status": output.get("status", ""),
        "hit_probe_ids": list(output.get("hit_probe_ids") or []),
        "last_hit_role": output.get("last_hit_role", ""),
        "first_unreached_role": output.get("first_unreached_role", ""),
        "evidence_ref": output.get("evidence_ref", ""),
        "recommended_action": _frontier_recommended_action(str(output.get("status") or "")),
        "reason": str(output.get("error") or output.get("first_unreached_role") or output.get("last_hit_role") or "")[:200],
    }
    # Keep the legacy fields consumed by feedback arbitration.
    record["status"] = str(output.get("status") or "")
    record["frontier"] = str(output.get("first_unreached_role") or output.get("last_hit_role") or "")
    probes.append(record)
    state.metadata[FRONTIER_PROBES] = probes[-8:]

    outcome = str(output.get("status") or "")
    if outcome in {"harness_not_reached", "parser_rejected", "dispatch_not_selected", "sink_not_reached", "sink_reached_trigger_unmet", "capability_error"}:
        kind = {
            "harness_not_reached": "path_not_reached",
            "parser_rejected": "path_not_reached",
            "dispatch_not_selected": "path_not_reached",
            "sink_not_reached": "path_not_reached",
            "sink_reached_trigger_unmet": "trigger_condition_not_satisfied",
            "capability_error": "frontier_unknown",
        }.get(outcome, "frontier_unknown")
        try:
            state.append_negative_evidence(
                kind=kind,
                objective_id=str(output.get("objective_id") or ""),
                ranked_path_id=str(output.get("path_id") or ""),
                summary=f"probe_runtime_frontier status={outcome}",
                avoid_next="Use the last-hit/first-unreached frontier before mutating the same field again.",
            )
        except Exception:
            pass

    bump_context_revision(state, "frontier_probes")
    bump_context_revision(state, "runtime_evidence")


def _frontier_recommended_action(status: str) -> str:
    return {
        "harness_not_reached": "extract_harness_protocol",
        "parser_rejected": "repair_carrier",
        "dispatch_not_selected": "localize_field",
        "sink_not_reached": "localize_field",
        "sink_reached_trigger_unmet": "localize_field",
        "capability_error": "extract_harness_protocol",
    }.get(status, "")


# ---------------------------------------------------------------------------
# WRITE handler
# ---------------------------------------------------------------------------

@register_handler("WRITE")
@register_handler("write_file")
@register_handler("create")
def handle_write_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Track PoC file creation from WRITE tool."""
    direct_paths: list[str] = []
    if isinstance(output, str) and "poc" in output.lower():
        direct_paths = [str(output)]
    elif isinstance(output, dict) and "path" in output:
        direct_paths = [str(output["path"])]
    if direct_paths:
        agent._register_direct_candidates(state, direct_paths)


# ---------------------------------------------------------------------------
# BASH handler
# ---------------------------------------------------------------------------

@register_handler("BASH")
@register_handler("run_command")
@register_handler("bash_v2")
def handle_bash_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Track command execution for error traces and PoC creation."""
    if isinstance(output, dict):
        rc = output.get("returncode", 0)
        stderr = output.get("stderr", "")
        if rc != 0 and stderr:
            state.last_error_trace = stderr
        elif rc != 0:
            state.last_error_trace = f"Exit code: {rc}"
        if rc == 0:
            command = str(output.get("command", "") or "")
            bash_paths = extract_poc_paths_from_bash(command, state)
            if bash_paths:
                agent._register_direct_candidates(state, bash_paths)


# ---------------------------------------------------------------------------
# Record attempt / reflection handlers
# ---------------------------------------------------------------------------

@register_handler("record_attempt")
def handle_record_attempt(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    state.pending_attempt_record = False


@register_handler("record_reflection")
def handle_record_reflection(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    state.pending_reflection_record = False
    agent._mark_failure_signature_reflected(state)


# ---------------------------------------------------------------------------
# Chain node / gate handlers
# ---------------------------------------------------------------------------

@register_handler("record_chain_node")
def handle_record_chain_node(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    _handle_chain_or_gate(state, "record_chain_node", output)


@register_handler("record_gate")
def handle_record_gate(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    _handle_chain_or_gate(state, "record_gate", output)


def _handle_chain_or_gate(state: CyberGymState, short_name: str, output: Any) -> None:
    if getattr(state, "pending_chain_checkpoint", False):
        if state.call_chain_nodes or state.call_chain_gates:
            state.pending_chain_checkpoint = False
    if getattr(state, "pending_gates_checkpoint", False):
        if state.call_chain_gates:
            state.pending_gates_checkpoint = False
    if short_name == "record_gate" and isinstance(output, dict):
        gate_desc = str(output.get("description") or "").strip()
        gate_cond = str(output.get("required_condition") or "").strip()
        if gate_desc and hasattr(state, "suggested_constraints"):
            state.suggested_constraints = [
                s for s in state.suggested_constraints
                if s.get("description", "") != gate_desc
            ]
        state.gate_board_last_changed_step = getattr(state, "current_step", 0) or 0
        if gate_desc and hasattr(state, "gate_evidence_brief"):
            brief = gate_cond[:80] if gate_cond else gate_desc[:80]
            state.gate_evidence_brief[gate_desc] = brief


# ---------------------------------------------------------------------------
# READ handler
# ---------------------------------------------------------------------------

@register_handler("READ")
@register_handler("read_file")
@register_handler("view")
@register_handler("file_read_v2")
@register_handler("read_file_range")
def handle_read_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Post-processing for READ tool results."""
    output_str = result.text if hasattr(result, 'text') else ""
    agent._track_match_read_follow(state, output)
    if output_str:
        agent._extract_findings_from_read(state, output_str)
    from ..core.metadata_keys import REPO_INDEX_V2
    structural_index = state.metadata.get(REPO_INDEX_V2)
    if isinstance(structural_index, dict) and state.harness_candidates:
        agent._resolve_harness_candidates(state, structural_index)
        state.input_format = agent._build_input_format_model(state)
    agent._confirm_constraints_from_read(state, output)
    agent._analyze_read_context(state, output)


# ---------------------------------------------------------------------------
# GREP handler
# ---------------------------------------------------------------------------

@register_handler("GREP")
@register_handler("grep")
@register_handler("grep_files")
@register_handler("grep_v2")
@register_handler("search")
def handle_grep_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    output_str = result.text if hasattr(result, 'text') else ""
    if output_str:
        agent._extract_findings_from_search(state, output_str)


# ---------------------------------------------------------------------------
# FindSymbols / CallsiteSearch handlers
# ---------------------------------------------------------------------------

@register_handler("FindSymbols")
@register_handler("find_symbols")
def handle_find_symbols_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    agent._infer_chain_from_search(state, "find_symbols", output)


@register_handler("CallsiteSearch")
@register_handler("callsite_search")
def handle_callsite_search_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    agent._infer_chain_from_search(state, "callsite_search", output)


# ---------------------------------------------------------------------------
# GLOB handler
# ---------------------------------------------------------------------------

@register_handler("GLOB")
@register_handler("glob")
def handle_glob_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    agent._capture_glob_metrics(state, output)
