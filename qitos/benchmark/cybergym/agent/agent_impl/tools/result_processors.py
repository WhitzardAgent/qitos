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
from ..core.metadata_keys import (
    FRONTIER_PROBES,
    LAST_FEEDBACK_ACTION,
    RUNTIME_EVIDENCE,
)


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

def _refresh_feedback_action_after_dynamic_result(agent: Any, state: CyberGymState) -> None:
    """Re-arbitrate after dynamic evidence or a fail-closed validation result."""
    from ..core.runtime_context_contract import bump_context_revision
    from ..feedback.arbitration import derive_feedback_action

    verification = getattr(state, "last_verification_result", {}) or {}
    failed_gate = ""
    classifier = getattr(agent, "_classify_failed_gate", None)
    if callable(classifier) and isinstance(verification, dict):
        try:
            failed_gate = str(classifier(dict(verification)) or "")
        except Exception:
            failed_gate = ""
    if not failed_gate and isinstance(verification, dict):
        failed_gate = str(verification.get("failed_gate") or "")
    if not failed_gate:
        failed_gate = "no_crash_unknown"

    action = derive_feedback_action(
        state=state,
        submit_result=verification if isinstance(verification, dict) else None,
        failed_gate=failed_gate,
    )
    state.metadata[LAST_FEEDBACK_ACTION] = action
    bump_context_revision(state, "feedback_action")

# run_candidate handler removed — tool unregistered due to zero diagnostic value.
# The gdb_debug handler still uses _refresh_feedback_action_after_dynamic_result.


@register_handler("gdb_debug")
def handle_gdb_debug_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Persist raw GDB debug evidence — no auto-classification."""
    if not isinstance(output, dict):
        _refresh_feedback_action_after_dynamic_result(agent, state)
        return

    from ..core.runtime_context_contract import bump_context_revision

    evidence_list = state.metadata.setdefault(RUNTIME_EVIDENCE, [])
    if not isinstance(evidence_list, list):
        evidence_list = []
        state.metadata[RUNTIME_EVIDENCE] = evidence_list

    evidence_id = f"rte_{len(evidence_list):04d}"
    record = {
        "evidence_id": evidence_id,
        "source_kind": "gdb_debug",
        "poc_path": output.get("poc_path", ""),
        "binary_path": output.get("binary_path", ""),
        "input_mode": output.get("input_mode", ""),
        "commands": list(output.get("commands") or []),
        "commands_stripped": output.get("commands_stripped", False),
        "timed_out": output.get("timed_out", False),
        "output_truncated": output.get("output_truncated", False),
        "returncode": output.get("returncode", -1),
        "inconclusive": output.get("inconclusive", False),
        "objective_id": output.get("objective_id", ""),
        "observed_at_step": int(getattr(state, "current_step", 0) or 0),
        "elapsed_ms": output.get("elapsed_ms", 0),
    }
    # Store output — GDB output can be 5-6KB with backtrace + variables,
    # which is critical for the model to reason about why PoC doesn't trigger.
    # Cap at 8000 chars to avoid bloat while preserving diagnostic value.
    output_text = str(output.get("output") or "")
    if len(output_text) > 8000:
        record["output_snippet"] = output_text[:3000] + "\n...[truncated]...\n" + output_text[-3000:]
    else:
        record["output"] = output_text
    evidence_list.append(record)
    state.metadata[RUNTIME_EVIDENCE] = evidence_list[-12:]

    # No auto-classification into sink_not_reached, capability_error, etc.
    # No negative_evidence generation.
    # The model interprets raw GDB output via Dynamic Evidence section.

    # GDB diagnostic budget: track per-candidate and total call counts
    MAX_GDB_PER_CANDIDATE = 3
    MAX_GDB_TOTAL = 8
    gdb_count = int(getattr(state, "gdb_call_count", 0) or 0) + 1
    state.gdb_call_count = gdb_count
    poc_path = str(output.get("poc_path", "") or "")
    current_diag = str(getattr(state, "current_diagnosis_candidate", "") or "")
    if poc_path and poc_path != current_diag:
        # Candidate changed — reset per-candidate counter
        state.current_diagnosis_candidate = poc_path
        state.gdb_calls_for_current_candidate = 1
    else:
        candidate_count = int(getattr(state, "gdb_calls_for_current_candidate", 0) or 0) + 1
        state.gdb_calls_for_current_candidate = candidate_count
    # Latch gdb_unavailable if budget exceeded
    if (int(getattr(state, "gdb_calls_for_current_candidate", 0) or 0) >= MAX_GDB_PER_CANDIDATE
            or gdb_count >= MAX_GDB_TOTAL):
        state.gdb_unavailable = True

    # Clear pending_reproduction flag (GDB diagnosis complete)
    from ..tools.dynamic_execution import _settle_reproduction
    if output.get("status") == "error":
        _settle_reproduction(state, latch=True)
    else:
        _settle_reproduction(state, latch=False)

    # Clear pending_diagnosis: dynamic diagnosis is complete
    if getattr(state, "pending_diagnosis", False):
        state.pending_diagnosis = False

    bump_context_revision(state, "runtime_evidence")
    _refresh_feedback_action_after_dynamic_result(agent, state)


# _frontier_recommended_action removed — gdb_debug returns raw facts,
# not auto-classified frontier statuses.


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
