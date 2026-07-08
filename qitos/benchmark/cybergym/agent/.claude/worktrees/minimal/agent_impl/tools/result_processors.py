"""Minimal tool-result processors for the Minimal CyberGym Agent.

Only handles the 9 tools: READ, GREP, GLOB, WRITE, BASH, GDB, SINK, GATE, SUBMIT_POC.
"""

from __future__ import annotations

from typing import Any, Callable

from ...state import CyberGymState
from ..core.fact_extraction import extract_poc_paths_from_bash
from ..core.metadata_keys import RUNTIME_EVIDENCE


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
# Submit PoC handler
# ---------------------------------------------------------------------------

@register_handler("submit_poc")
def handle_submit_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    agent._process_submit_result(state, result, output)


# ---------------------------------------------------------------------------
# GDB handler — simplified, no budget limits
# ---------------------------------------------------------------------------

@register_handler("GDB")
@register_handler("gdb_debug")
def handle_gdb_debug_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    if not isinstance(output, dict):
        return

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
        "timed_out": output.get("timed_out", False),
        "returncode": output.get("returncode", -1),
    }
    output_text = str(output.get("output") or "")
    if len(output_text) > 8000:
        record["output_snippet"] = output_text[:3000] + "\n...[truncated]...\n" + output_text[-3000:]
    else:
        record["output"] = output_text
    evidence_list.append(record)
    state.metadata[RUNTIME_EVIDENCE] = evidence_list[-12:]

    # Track GDB call count (no budget limit in minimal)
    state.gdb_call_count = int(getattr(state, "gdb_call_count", 0) or 0) + 1


# ---------------------------------------------------------------------------
# WRITE handler
# ---------------------------------------------------------------------------

@register_handler("WRITE")
@register_handler("write")
@register_handler("write_file")
@register_handler("create")
def handle_write_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Track PoC file paths from WRITE tool."""
    if isinstance(output, dict) and "path" in output:
        path = str(output["path"])
        if "poc" in path.lower():
            state.last_submitted_poc_path = path


# ---------------------------------------------------------------------------
# BASH handler
# ---------------------------------------------------------------------------

@register_handler("BASH")
@register_handler("bash")
@register_handler("run_command")
@register_handler("bash_v2")
def handle_bash_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Track command execution for error traces and PoC paths."""
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
                state.last_submitted_poc_path = bash_paths[-1]


# ---------------------------------------------------------------------------
# GATE handler — update gate_board_last_changed_step
# ---------------------------------------------------------------------------

@register_handler("GATE")
def handle_gate_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    state.gate_board_last_changed_step = int(getattr(state, "current_step", 0) or 0)


# ---------------------------------------------------------------------------
# SINK handler — no special processing needed
# ---------------------------------------------------------------------------

@register_handler("SINK")
def handle_sink_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    pass


# ---------------------------------------------------------------------------
# READ handler — simplified
# ---------------------------------------------------------------------------

@register_handler("READ")
@register_handler("read")
@register_handler("read_file")
@register_handler("view")
@register_handler("file_read_v2")
@register_handler("read_file_range")
def handle_read_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    """Minimal post-processing for READ results."""
    pass


# ---------------------------------------------------------------------------
# GREP handler — simplified
# ---------------------------------------------------------------------------

@register_handler("GREP")
@register_handler("grep")
@register_handler("grep_files")
@register_handler("grep_v2")
@register_handler("search")
def handle_grep_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    pass


# ---------------------------------------------------------------------------
# GLOB handler — simplified
# ---------------------------------------------------------------------------

@register_handler("GLOB")
@register_handler("glob")
def handle_glob_result(agent: Any, state: CyberGymState, result: Any, output: Any) -> None:
    pass
