"""Lightweight task-local tracking tools written by the model itself."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult

PROJECT_MEMORY_ROOT = Path(".agent") / "memory" / "project"
STRATEGY_MEMORY_DIR = PROJECT_MEMORY_ROOT / "strategy"


def _workspace_root(runtime_context: Optional[Dict[str, Any]]) -> Optional[Path]:
    state = (runtime_context or {}).get("state")
    root = getattr(state, "workspace_root", "") if state is not None else ""
    return Path(root) if root else None


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _clip(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _project_memory_root(root: Path) -> Path:
    return root / PROJECT_MEMORY_ROOT


def _append_project_index(root: Path, *, kind: str, path: str, chars: int = 0) -> None:
    project_root = _project_memory_root(root)
    project_root.mkdir(parents=True, exist_ok=True)
    index_path = project_root / "INDEX.md"
    if not index_path.exists():
        index_path.write_text(
            "# Externalized Context Index\n\n"
            "Paths below are relative to the task workspace.\n",
            encoding="utf-8",
        )
    line = f"- kind={kind} step=0 path={path} chars={int(chars)}"
    existing = index_path.read_text(encoding="utf-8").splitlines()
    path_marker = f"path={path} "
    if any(path_marker in item for item in existing):
        return
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _strategy_dir(root: Path) -> Path:
    return root / STRATEGY_MEMORY_DIR


def _strategy_ledger_path() -> str:
    return (STRATEGY_MEMORY_DIR / "LEDGER.md").as_posix()


def _render_strategy_ledger(state: Any) -> str:
    attempts = [
        item for item in list(getattr(state, "attempt_history", []) or [])
        if isinstance(item, dict)
    ][-12:]
    reflections = [
        item for item in list(getattr(state, "reflection_history", []) or [])
        if isinstance(item, dict)
    ][-8:]

    lines = [
        "# Strategy Ledger",
        "",
        "This file records compact strategy-level attempt and reflection history.",
        "",
        "## Attempts",
    ]
    if attempts:
        for item in attempts:
            family = _clip(item.get("strategy_family") or "?", 80)
            path = _clip(item.get("poc_path") or "?", 120)
            result = _clip(item.get("observed_result") or "?", 100)
            feedback = _clip(item.get("stable_feedback") or "", 140)
            next_hypothesis = _clip(item.get("next_hypothesis") or "", 140)
            suffix = f"; feedback={feedback}" if feedback else ""
            if next_hypothesis:
                suffix += f"; next={next_hypothesis}"
            lines.append(f"- `{family}` path=`{path}` result={result}{suffix}")
    else:
        lines.append("- No attempts recorded yet.")

    lines.extend(["", "## Reflections"])
    if reflections:
        for item in reflections:
            summary = _clip(item.get("summary") or "", 180)
            next_step = _clip(item.get("next_step") or "", 160)
            reinvestigate = " yes" if bool(item.get("request_reinvestigation")) else " no"
            lines.append(f"- summary={summary}; next={next_step}; reinvestigate={reinvestigate}")
    else:
        note = _clip(getattr(state, "reflection_note", "") or "", 220)
        if note:
            lines.append(f"- {note}")
        else:
            lines.append("- No reflections recorded yet.")

    return "\n".join(lines).rstrip() + "\n"


def _write_strategy_memory(root: Path, state: Any) -> None:
    strategy_dir = _strategy_dir(root)
    strategy_dir.mkdir(parents=True, exist_ok=True)
    ledger = strategy_dir / "LEDGER.md"
    content = _render_strategy_ledger(state)
    ledger.write_text(content, encoding="utf-8")
    _append_project_index(
        root,
        kind="strategy",
        path=_strategy_ledger_path(),
        chars=len(content),
    )


def _append_exploration_note(
    state: Any,
    runtime_context: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> None:
    if state is not None:
        notes = list(getattr(state, "exploration_notes", []) or [])
        notes.append(payload)
        state.exploration_notes = notes[-20:]
    root = _workspace_root(runtime_context)
    if root is not None:
        _append_jsonl(root / ".cybergym" / "exploration_notes.jsonl", payload)


def _validate_phase_transition(current: str, target: str, state: Any) -> tuple[bool, str]:
    """Check if a phase transition is allowed given current state."""
    ALLOWED = {
        ("exploration", "investigation"): None,       # always OK
        ("exploration", "formulation"): "exploration_to_formulation",
        ("investigation", "exploration"): None,        # always OK (re-explore)
        ("investigation", "formulation"): None,        # always OK
        ("formulation", "investigation"): "formulation_to_investigation",
        ("formulation", "exploration"): "formulation_to_exploration",
        ("verification", "investigation"): None,       # already exists
        ("verification", "formulation"): None,         # already exists
    }

    key = (current, target)
    if key not in ALLOWED:
        return False, f"Transition {current} → {target} is not allowed"

    constraint = ALLOWED[key]
    if constraint is None:
        return True, ""

    # Check specific constraints
    if constraint == "exploration_to_formulation":
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        gates = list(getattr(state, "call_chain_gates", []) or [])
        has_chain = len(nodes) >= 2
        has_gate = any(g.status == "confirmed" for g in gates)
        has_hypothesis = bool(getattr(state, "trigger_hypothesis", ""))
        if not (has_chain and has_gate):
            return False, "Need 2+ chain nodes and 1+ confirmed gate to skip investigation"
        if not has_hypothesis:
            return False, "Set trigger_hypothesis first (call record_hypothesis)"

    if constraint == "formulation_to_investigation":
        has_result = bool(getattr(state, "last_verification_result", ""))
        if not has_result and not getattr(state, "poc_attempts", 0):
            return False, "Can only return to investigation after at least one PoC attempt"

    if constraint == "formulation_to_exploration":
        attempts = int(getattr(state, "poc_attempts", 0) or 0)
        best = float(getattr(state, "best_poc_score", 0) or 0)
        if attempts < 2 or best > 0:
            return False, "Can only return to exploration after 2+ failed attempts with score 0"

    return True, ""


class SwitchPhaseTool(BaseTool):
    """Switch the agent to a different phase of the workflow.

    The LLM calls this when it realizes the current phase is wrong for what
    it needs to do next — e.g., going back to exploration when investigation
    reveals it needs more code understanding, or skipping ahead to formulation
    when auto-analysis has already built a complete chain.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="switch_phase",
                description=(
                    "Switch to a different phase of the agent workflow. Use this when "
                    "you realize the current phase is not the right one for what you "
                    "need to do next. Examples: go back to exploration when investigation "
                    "reveals you need more code understanding, or skip ahead to formulation "
                    "when auto-analysis has already built a complete chain."
                ),
                parameters={
                    "target_phase": {
                        "type": "string",
                        "enum": ["exploration", "investigation", "formulation"],
                        "description": "Target phase to switch to",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you need to switch phase (e.g., 'Need to re-read code to understand the dispatch path')",
                    },
                },
                required=["target_phase", "reason"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        target = str(args.get("target_phase", "")).strip()
        if target not in ("exploration", "investigation", "formulation"):
            return ToolValidationResult.fail("target_phase must be exploration, investigation, or formulation")
        if not str(args.get("reason", "")).strip():
            return ToolValidationResult.fail("reason is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = (runtime_context or {}).get("state")
        target = str(args.get("target_phase", "")).strip()
        reason = str(args.get("reason", "")).strip()
        current = str(getattr(state, "current_phase", "") or "")

        # Validate transition
        allowed, block_reason = _validate_phase_transition(current, target, state)
        if not allowed:
            return {"status": "rejected", "reason": block_reason}

        # Apply transition
        state.current_phase = target
        state.phase_enter_step = int(getattr(state, "current_step", 0) or 0)
        state.phase_local_steps = 0
        state.phase_submissions = 0
        state.phase_read_actions = 0
        state.repeated_read_target = ""
        state.repeated_read_count = 0

        # Set phase-specific flags
        if target == "exploration":
            state.exploration_complete = False
        if target == "investigation":
            state.reinvestigate_requested = False
        if target == "formulation":
            if not state.trigger_hypothesis:
                state.trigger_hypothesis = reason

        # Signal to reduce() that a manual switch happened this step
        # so PhaseEngine.advance() does not overwrite it.
        state.metadata["_manual_phase_switch"] = target

        # Append exploration note
        _append_exploration_note(state, runtime_context, {
            "note_type": "phase_switch",
            "from": current,
            "to": target,
            "reason": _clip(reason, 120),
        })

        return {"status": "success", "from": current, "to": target, "reason": reason}


class RecordHypothesisTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_hypothesis",
                description=(
                    "Record the current exploit hypothesis as a short exploration note. "
                    "Use this when you settle on a candidate exploit family and target surface."
                ),
                parameters={
                    "strategy_family": {"type": "string", "description": "Short name for the exploit family"},
                    "target_surface": {"type": "string", "description": "Target parser, function, or code path"},
                    "reason": {"type": "string", "description": "Why this family should trigger the bug"},
                    "next_test": {"type": "string", "description": "Next candidate or mutation to try"},
                },
                required=["strategy_family", "target_surface", "reason"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("strategy_family") or "").strip():
            return ToolValidationResult.fail("strategy_family is required")
        if not str(args.get("target_surface") or "").strip():
            return ToolValidationResult.fail("target_surface is required")
        if not str(args.get("reason") or "").strip():
            return ToolValidationResult.fail("reason is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = (runtime_context or {}).get("state")
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "note_type": "hypothesis",
            "strategy_family": str(args.get("strategy_family") or ""),
            "target_surface": str(args.get("target_surface") or ""),
            "reason": str(args.get("reason") or ""),
            "next_test": str(args.get("next_test") or ""),
        }
        _append_exploration_note(state, runtime_context, payload)
        return {"status": "success", "recorded": payload}


class RecordAttemptTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_attempt",
                description=(
                    "Record the latest PoC attempt in a structured ledger. Use this "
                    "after each submit_poc so the agent remembers which PoC path and "
                    "strategy family have already been tried."
                ),
                parameters={
                    "poc_path": {"type": "string", "description": "PoC path that was just submitted"},
                    "strategy_family": {"type": "string", "description": "Short name for the PoC idea/family"},
                    "derived_from": {"type": "string", "description": "What this attempt was derived from"},
                    "mutation_note": {"type": "string", "description": "What changed in this attempt"},
                    "expected_trigger": {"type": "string", "description": "What trigger was expected"},
                    "observed_result": {"type": "string", "description": "Short summary of the observed result"},
                    "stable_feedback": {"type": "string", "description": "Stable verification hint or key stderr line"},
                    "next_hypothesis": {"type": "string", "description": "What to try next"},
                },
                required=["poc_path", "strategy_family", "observed_result"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("poc_path") or "").strip():
            return ToolValidationResult.fail("poc_path is required")
        if not str(args.get("strategy_family") or "").strip():
            return ToolValidationResult.fail("strategy_family is required")
        if not str(args.get("observed_result") or "").strip():
            return ToolValidationResult.fail("observed_result is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = (runtime_context or {}).get("state")
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "poc_path": str(args.get("poc_path") or ""),
            "strategy_family": str(args.get("strategy_family") or ""),
            "derived_from": str(args.get("derived_from") or ""),
            "mutation_note": str(args.get("mutation_note") or ""),
            "expected_trigger": str(args.get("expected_trigger") or ""),
            "observed_result": str(args.get("observed_result") or ""),
            "stable_feedback": str(args.get("stable_feedback") or ""),
            "next_hypothesis": str(args.get("next_hypothesis") or ""),
        }
        if state is not None:
            state.attempt_history.append(payload)
            state.attempt_history = state.attempt_history[-12:]
            state.pending_attempt_record = False
            _append_exploration_note(
                state,
                runtime_context,
                {
                    "ts": payload["ts"],
                    "note_type": "submission",
                    "strategy_family": payload["strategy_family"],
                    "poc_path": payload["poc_path"],
                    "observed_result": payload["observed_result"],
                    "stable_feedback": payload["stable_feedback"],
                    "next_hypothesis": payload["next_hypothesis"],
                },
            )
        root = _workspace_root(runtime_context)
        if root is not None:
            _append_jsonl(root / ".cybergym" / "attempt_history.jsonl", payload)
            _append_jsonl(_strategy_dir(root) / "attempts.jsonl", payload)
            if state is not None:
                _write_strategy_memory(root, state)
        return {"status": "success", "recorded": payload}


class RecordReflectionTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_reflection",
                description=(
                    "Record a short self-review after repeated failures. Use this to "
                    "summarize what has been tried, why it failed, and whether to "
                    "re-investigate before continuing."
                ),
                parameters={
                    "summary": {"type": "string", "description": "What has been tried and what was learned"},
                    "next_step": {"type": "string", "description": "What the agent should do next"},
                    "request_reinvestigation": {
                        "type": "boolean",
                        "description": "Whether to return to investigation instead of continuing direct iteration",
                    },
                },
                required=["summary", "next_step"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("summary") or "").strip():
            return ToolValidationResult.fail("summary is required")
        if not str(args.get("next_step") or "").strip():
            return ToolValidationResult.fail("next_step is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = (runtime_context or {}).get("state")
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "summary": str(args.get("summary") or ""),
            "next_step": str(args.get("next_step") or ""),
            "request_reinvestigation": bool(args.get("request_reinvestigation", False)),
        }
        if state is not None:
            state.reflection_note = (
                f"{payload['summary']} Next: {payload['next_step']}"
            ).strip()
            history = list(getattr(state, "reflection_history", []) or [])
            history.append(payload)
            state.reflection_history = history[-12:]
            state.reinvestigate_requested = payload["request_reinvestigation"]
            state.pending_reflection = False
            _append_exploration_note(
                state,
                runtime_context,
                {
                    "ts": payload["ts"],
                    "note_type": "reflection",
                    "summary": payload["summary"],
                    "next_step": payload["next_step"],
                },
            )
        root = _workspace_root(runtime_context)
        if root is not None:
            _append_jsonl(root / ".cybergym" / "reflections.jsonl", payload)
            _append_jsonl(_strategy_dir(root) / "reflections.jsonl", payload)
            if state is not None:
                _write_strategy_memory(root, state)
        return {"status": "success", "recorded": payload}


class RecordChainNodeTool(BaseTool):
    """Record a node in the entry-to-sink call chain.

    The LLM calls this after understanding a function's role in the data
    flow from harness entry to vulnerability sink.  Unlike auto-extracted
    constraints, this requires the LLM's contextual understanding of
    whether the function is on the relevant path.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_chain_node",
                description=(
                    "Record a function in the entry-to-sink call chain. "
                    "Use after READ-ing code and understanding how data flows "
                    "from the harness entry to the vulnerable function. "
                    "Each node represents one hop in the call chain."
                ),
                parameters={
                    "function": {"type": "string", "description": "Function name"},
                    "location": {"type": "string", "description": "Source location (e.g., 'attribute.c:1880')"},
                    "role": {
                        "type": "string",
                        "description": "Role in the chain: 'entry' (harness), 'parser' (format decode), 'dispatch' (branch router), 'guard' (condition check), or 'sink' (vulnerable point)",
                    },
                    "description": {"type": "string", "description": "What this function does in the data flow"},
                    "status": {
                        "type": "string",
                        "description": "'confirmed' (verified from source) or 'inferred' (best guess)",
                    },
                    "sink_id": {
                        "type": "string",
                        "description": "Optional sink candidate identifier (function@location). Defaults to primary sink.",
                    },
                },
                required=["function", "location", "role", "description"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("function") or "").strip():
            return ToolValidationResult.fail("function is required")
        if not str(args.get("location") or "").strip():
            return ToolValidationResult.fail("location is required")
        role = str(args.get("role") or "").strip()
        if role not in ("entry", "parser", "dispatch", "guard", "sink"):
            return ToolValidationResult.fail("role must be one of: entry, parser, dispatch, guard, sink")
        if not str(args.get("description") or "").strip():
            return ToolValidationResult.fail("description is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .state import ChainNode

        state = (runtime_context or {}).get("state")
        function = str(args.get("function") or "").strip()
        location = str(args.get("location") or "").strip()
        role = str(args.get("role") or "parser").strip()
        description = str(args.get("description") or "").strip()
        status = str(args.get("status") or "inferred").strip()
        sink_id = str(args.get("sink_id") or "").strip()
        if not sink_id and state is not None:
            sink_id = state._primary_sink_id()

        if state is not None:
            # Deduplicate by function@location (within same sink_id)
            existing_keys = {f"{n.function}@{n.location}@{n.sink_id}" for n in state.call_chain_nodes}
            key = f"{function}@{location}@{sink_id}"
            if key in existing_keys:
                # Update existing node
                for n in state.call_chain_nodes:
                    if f"{n.function}@{n.location}@{n.sink_id}" == key:
                        n.role = role
                        n.description = description
                        n.status = status
                        break
            else:
                # Assign order: max existing + 1 within same sink_id
                same_sink_orders = [
                    n.order for n in state.call_chain_nodes
                    if n.sink_id == sink_id or (not n.sink_id and sink_id == state._primary_sink_id())
                ]
                max_order = max(same_sink_orders, default=-1)
                state.call_chain_nodes.append(ChainNode(
                    location=location,
                    function=function,
                    role=role,
                    description=description,
                    status=status,
                    evidence=f"record_chain_node by agent",
                    order=max_order + 1,
                    sink_id=sink_id,
                ))
            # Cap
            if len(state.call_chain_nodes) > 20:
                state.call_chain_nodes = state.call_chain_nodes[-20:]

            # Auto-populate sink candidate when role="sink" and no
            # matching SinkCandidate exists yet.  This catches the common
            # case where the agent records record_chain_node(role="sink")
            # but forgets to call record_sink_candidate.
            if role == "sink":
                existing = None
                for c in state.sink_candidates:
                    if c.function.lower() == function.lower() and c.status != "eliminated":
                        existing = c
                        break
                if existing is None:
                    from .state import SinkCandidate
                    raw_file, sep, raw_line = location.rpartition(":")
                    file_name = raw_file if sep and raw_line.isdigit() else location
                    line_num = int(raw_line) if sep and raw_line.isdigit() else 0
                    state.sink_candidates.append(SinkCandidate(
                        function=function,
                        location=location,
                        confidence=0.6,
                        evidence=f"Auto-created from chain node: {description}",
                        status="candidate",
                        source="model_candidate",
                        file=file_name,
                        line=line_num,
                        reason=description,
                        metadata={"requires_review": False, "confirmed_via": "record_chain_node"},
                    ))
                    created = state.sink_candidates[-1]
                    import hashlib
                    material = f"{created.repository_id}|{created.file}|{created.line}|{created.function}||"
                    created.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
                    state.active_sink_candidate_id = created.candidate_id
                    state.active_sink_id = state._primary_sink_id()
                    state.analysis_status = "TARGET_PROPOSED"
                    state.metadata["_pending_sink_analysis"] = created.candidate_id
                    state.pending_sink_checkpoint = False

            # Persist to exploration notes (like record_hypothesis/reflection)
            _append_exploration_note(
                state, runtime_context,
                {
                    "note_type": "chain_node",
                    "function": function,
                    "location": location,
                    "role": role,
                    "status": status,
                },
            )

        return {"status": "success", "function": function, "location": location, "role": role}


class RecordGateTool(BaseTool):
    """Record a path constraint (gate) on the call chain.

    The LLM calls this after understanding a condition that input must
    satisfy to pass through a point in the call chain.  Gates are the
    core of the constraint propagation system — they track what the PoC
    must achieve, and when a submission fails, gates are *refuted* (not
    deleted) so the agent learns from failures.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_gate",
                description=(
                    "Record a path constraint (gate) that the PoC must satisfy. "
                    "Use after READ-ing code and identifying a concrete condition "
                    "that input must meet to reach the vulnerable code. "
                    "Examples: 'JPEG must have valid APP1 marker', 'IFD tag must "
                    "be in range [0x0100, 0xFFFF]', 'format_bytes[f]*c must overflow "
                    "on 32-bit'. Each gate belongs to a chain node."
                ),
                parameters={
                    "node_function": {
                        "type": "string",
                        "description": "Function name where this gate applies (must match a recorded chain node)",
                    },
                    "gate_type": {
                        "type": "string",
                        "description": "Type: 'format_gate' (magic bytes/header), 'path_gate' (branch condition), 'dispatch_gate' (switch/routing), 'bounds_gate' (size/overflow), 'value_gate' (specific field value)",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the gate requires (e.g., 'Must match Exif\\0\\0 magic at APP1 segment')",
                    },
                    "required_condition": {
                        "type": "string",
                        "description": "Positive condition for PoC construction (e.g., 'APP1 segment starts with 45 78 69 66 00 00')",
                    },
                    "status": {
                        "type": "string",
                        "description": "'confirmed' (verified from source code), 'inferred' (best guess from context)",
                    },
                    "role": {
                        "type": "string",
                        "description": "Optional analyzer role: reachability, trigger, safety_invariant, dataflow, or hazard",
                    },
                    "path_id": {
                        "type": "string",
                        "description": "Optional callsite/sink path identifier from Suggested Constraints",
                    },
                    "source_span": {
                        "type": "object",
                        "description": "Optional source span copied from a source-backed suggestion",
                    },
                    "sink_id": {
                        "type": "string",
                        "description": "Optional sink candidate identifier (function@location). Defaults to primary sink.",
                    },
                },
                required=["node_function", "gate_type", "description", "required_condition"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("node_function") or "").strip():
            return ToolValidationResult.fail("node_function is required")
        gate_type = str(args.get("gate_type") or "").strip()
        if gate_type not in ("format_gate", "path_gate", "dispatch_gate", "bounds_gate", "value_gate"):
            return ToolValidationResult.fail("gate_type must be one of: format_gate, path_gate, dispatch_gate, bounds_gate, value_gate")
        if not str(args.get("description") or "").strip():
            return ToolValidationResult.fail("description is required")
        if not str(args.get("required_condition") or "").strip():
            return ToolValidationResult.fail("required_condition is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .state import ChainGate

        state = (runtime_context or {}).get("state")
        node_function = str(args.get("node_function") or "").strip()
        gate_type = str(args.get("gate_type") or "path_gate").strip()
        description = str(args.get("description") or "").strip()
        required_condition = str(args.get("required_condition") or "").strip()
        status = str(args.get("status") or "inferred").strip()
        role = str(args.get("role") or "reachability").strip()
        path_id = str(args.get("path_id") or "").strip()
        source_span = args.get("source_span") if isinstance(args.get("source_span"), dict) else {}
        sink_id = str(args.get("sink_id") or "").strip()
        if not sink_id and state is not None:
            sink_id = state._primary_sink_id()

        if state is not None:
            # Find the node_order for the matching chain node (consider sink_id)
            node_order = 0
            primary = state._primary_sink_id()
            for n in state.call_chain_nodes:
                if n.function == node_function and (
                    n.sink_id == sink_id
                    or (not n.sink_id and sink_id == primary)
                    or not sink_id
                ):
                    node_order = n.order
                    break

            # Deduplicate by description
            existing_descs = {g.description for g in state.call_chain_gates}
            if description in existing_descs:
                # Update existing gate
                for g in state.call_chain_gates:
                    if g.description == description:
                        g.gate_type = gate_type
                        g.required_condition = required_condition
                        g.status = status
                        g.node_order = node_order
                        g.role = role
                        g.path_id = path_id
                        g.source_span = dict(source_span)
                        break
            else:
                state.call_chain_gates.append(ChainGate(
                    node_order=node_order,
                    gate_type=gate_type,
                    description=description,
                    required_condition=required_condition,
                    status=status,
                    evidence=f"record_gate by agent",
                    repair_hint="",
                    role=role,
                    path_id=path_id,
                    source_span=dict(source_span),
                    sink_id=sink_id,
                ))
            # Cap
            if len(state.call_chain_gates) > 40:
                state.call_chain_gates = state.call_chain_gates[-40:]

            # Persist to exploration notes (like record_hypothesis/reflection)
            _append_exploration_note(
                state, runtime_context,
                {
                    "note_type": "chain_gate",
                    "gate_type": gate_type,
                    "description": _clip(description, 120),
                    "status": status,
                    "role": role,
                    "path_id": path_id,
                },
            )

        return {
            "status": "success",
            "gate_type": gate_type,
            "description": _clip(description, 100),
            "required_condition": required_condition,
            "role": role,
            "path_id": path_id,
        }


class RecordSinkCandidateTool(BaseTool):
    """Record a sink candidate proposed by the LLM after reading code.

    The LLM calls this when it identifies a function that is likely the
    vulnerability entry point.  This replaces the noisy regex-based extraction
    from description text with code-informed proposals.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="record_sink_candidate",
                description=(
                    "Record a sink candidate — a function you believe is the "
                    "vulnerability entry point — after reading and understanding "
                    "the code. Use this when you identify a vulnerable function "
                    "that is not already in the Sink Candidates list, or to "
                    "upgrade confidence/evidence for an existing candidate."
                ),
                parameters={
                    "function": {
                        "type": "string",
                        "description": "Function name that is the vulnerability sink",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Why this function is a sink (e.g., 'READ attribute.c:1905 — unchecked memcpy with user-controlled size')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Source location (e.g., 'attribute.c:1880')",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0.0-1.0. Suggested: 0.7 for confirmed from source, 0.5 for strong evidence, 0.4 for plausible.",
                    },
                    "callee": {"type": "string", "description": "Optional target callee at the sink callsite"},
                    "expression": {"type": "string", "description": "Optional sink expression"},
                    "category": {"type": "string", "description": "Optional vulnerability/sink category"},
                    "candidate_role": {"type": "string", "description": "Optional role: crash_site | causal_site | path_anchor | dangerous_primitive | unknown"},
                    "ranked_path_id": {"type": "string", "description": "Optional path_id from Vulnerability Path when reviewing a static candidate"},
                    "source_span": {"type": "object", "description": "Optional source span {file,line,end_line}"},
                    "paired_with": {"type": "string", "description": "Optional paired candidate/path id for UAF/uninit/integer/overlap evidence"},
                },
                required=["function", "evidence"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("function") or "").strip():
            return ToolValidationResult.fail("function is required")
        if not str(args.get("evidence") or "").strip():
            return ToolValidationResult.fail("evidence is required")
        conf = args.get("confidence")
        if conf is not None:
            try:
                c = float(conf)
                if not (0.0 <= c <= 1.0):
                    return ToolValidationResult.fail("confidence must be between 0.0 and 1.0")
            except (TypeError, ValueError):
                return ToolValidationResult.fail("confidence must be a number")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .state import SinkCandidate

        state = (runtime_context or {}).get("state")
        func_name = str(args.get("function") or "").strip()
        evidence = str(args.get("evidence") or "").strip()
        location = str(args.get("location") or "").strip()
        confidence = float(args.get("confidence") or 0.5)
        confidence = max(0.0, min(1.0, confidence))
        callee = str(args.get("callee") or "").strip()
        expression = str(args.get("expression") or "").strip()
        category = str(args.get("category") or "").strip()
        candidate_role = str(args.get("candidate_role") or "crash_site").strip() or "crash_site"
        if candidate_role not in {"crash_site", "causal_site", "path_anchor", "dangerous_primitive", "unknown"}:
            candidate_role = "unknown"
        ranked_path_id = str(args.get("ranked_path_id") or "").strip()
        source_span = args.get("source_span") if isinstance(args.get("source_span"), dict) else {}
        paired_with = str(args.get("paired_with") or "").strip()
        file_name, line = "", 0
        raw_file, sep, raw_line = location.rpartition(":")
        if sep and raw_line.isdigit():
            file_name, line = raw_file, int(raw_line)
        elif location:
            file_name = location

        action = "created"

        if state is not None:
            # Entry-point sink suppression: cap confidence and don't clear checkpoint
            from .analysis.vuln_patterns import is_entry_point_function
            is_entry = is_entry_point_function(func_name)
            if is_entry:
                confidence = min(confidence, 0.2)
                state.metadata["entry_point_sink_recorded"] = True
            else:
                # Clear sink checkpoint on successful recording of a real sink
                state.pending_sink_checkpoint = False
                state.sink_hypothesis_source = "model_candidate"
            # Case-insensitive lookup for existing candidate
            live = [c for c in state.sink_candidates if c.status != "eliminated"]
            existing = None
            if ranked_path_id:
                existing = next(
                    (c for c in live if str((c.metadata or {}).get("ranked_path_id") or "") == ranked_path_id),
                    None,
                )
            if existing is None:
                existing = next((c for c in live if c.function.lower() == func_name.lower()), None)
            if existing is None:
                leaf_matches = [
                    c for c in live
                    if c.function.rsplit("::", 1)[-1].lower() == func_name.rsplit("::", 1)[-1].lower()
                ]
                if len(leaf_matches) == 1:
                    existing = leaf_matches[0]

            if existing is not None:
                action = "updated"
                if file_name:
                    existing.file, existing.line = file_name, line
                    existing.location = location
                if callee:
                    existing.callee = callee
                if expression:
                    existing.expression = expression
                if category:
                    existing.category = category
                existing.reason = evidence
                if existing.source in {"description", "description_symbol", "harness_chain", "static_navigation", "graph_auto_deepen"}:
                    # Upgrade noisy regex candidate to LLM-proposed
                    existing.source = "model_candidate"
                    existing.evidence = evidence
                    if location:
                        existing.location = location
                    existing.confidence = max(existing.confidence, confidence)
                else:
                    # Update existing candidate — upgrade confidence if higher
                    if confidence > existing.confidence:
                        existing.confidence = confidence
                    # Append evidence if different
                    if evidence not in existing.evidence:
                        existing.evidence = (
                            existing.evidence + "; " + evidence
                            if existing.evidence
                            else evidence
                        )
                    if location and not existing.location:
                        existing.location = location
                existing.status = "candidate"
                existing.metadata = dict(existing.metadata or {})
                existing.metadata.update({
                    "requires_review": False,
                    "reviewed": True,
                    "confirmed_via": "record_sink_candidate",
                    "selection_status": "active" if candidate_role != "path_anchor" else "reviewed_anchor",
                    "candidate_role": candidate_role,
                    "ranked_path_id": ranked_path_id or existing.metadata.get("ranked_path_id", ""),
                    "source_span": dict(source_span or existing.metadata.get("source_span") or {}),
                    "paired_with": paired_with or existing.metadata.get("paired_with", ""),
                    "needs_downstream_endpoint": candidate_role == "path_anchor",
                })
            else:
                state.sink_candidates.append(SinkCandidate(
                    function=func_name,
                    location=location,
                    confidence=confidence,
                    evidence=evidence,
                    status="candidate",
                    source="model_candidate",
                    file=file_name,
                    line=line,
                    callee=callee,
                    expression=expression,
                    category=category,
                    reason=evidence,
                    metadata={
                        "requires_review": False,
                        "reviewed": True,
                        "confirmed_via": "record_sink_candidate",
                        "selection_status": "active" if candidate_role != "path_anchor" else "reviewed_anchor",
                        "candidate_role": candidate_role,
                        "ranked_path_id": ranked_path_id,
                        "source_span": dict(source_span),
                        "paired_with": paired_with,
                        "needs_downstream_endpoint": candidate_role == "path_anchor",
                    },
                ))

            confirmed = existing if existing is not None else state.sink_candidates[-1]
            confirmed.source = "model_candidate"
            confirmed.status = "candidate"
            confirmed.metadata = dict(confirmed.metadata or {})
            confirmed.metadata.update({
                "requires_review": False,
                "reviewed": True,
                "confirmed_via": "record_sink_candidate",
                "selection_status": "active" if candidate_role != "path_anchor" else "reviewed_anchor",
                "candidate_role": candidate_role,
                "ranked_path_id": ranked_path_id or confirmed.metadata.get("ranked_path_id", ""),
                "source_span": dict(source_span or confirmed.metadata.get("source_span") or {}),
                "paired_with": paired_with or confirmed.metadata.get("paired_with", ""),
                "needs_downstream_endpoint": candidate_role == "path_anchor",
            })

            # Recalculate active sink if top candidate changed
            state.active_sink_id = state._primary_sink_id()
            selected = confirmed
            if not selected.candidate_id:
                import hashlib
                material = f"{selected.repository_id}|{selected.file}|{selected.line}|{selected.function}|{selected.callee}|{selected.expression}"
                selected.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
            if candidate_role != "path_anchor":
                state.active_sink_candidate_id = selected.candidate_id
            state.analysis_status = "TARGET_PROPOSED"
            # A structured sink candidate is the authoritative trigger for
            # repository-level analysis.  Do not depend on deployment-specific
            # environment flags: that caused most remote candidates to receive
            # no enrichment at all.
            if candidate_role in {"crash_site", "causal_site", "dangerous_primitive", "unknown"}:
                state.metadata["_pending_sink_analysis"] = selected.candidate_id

            # Cap at 12 candidates
            if len(state.sink_candidates) > 12:
                # Keep eliminated ones at the end, then trim
                active = [c for c in state.sink_candidates if c.status != "eliminated"]
                eliminated = [c for c in state.sink_candidates if c.status == "eliminated"]
                state.sink_candidates = active[-12:] + eliminated

            _append_exploration_note(
                state, runtime_context,
                {
                    "note_type": "sink_candidate",
                    "function": func_name,
                    "confidence": confidence,
                    "action": action,
                    "candidate_role": candidate_role,
                    "ranked_path_id": ranked_path_id,
                    "evidence": _clip(evidence, 120),
                },
            )

        return {
            "status": "success",
            "function": func_name,
            "confidence": confidence,
            "action": action,
            "candidate_role": candidate_role,
            "ranked_path_id": ranked_path_id,
            "evidence": _clip(evidence, 100),
            "candidate_id": getattr(selected, "candidate_id", "") if state is not None else "",
            "analysis_triggered": state is not None,
        }


class AnalyzeDescriptionTool(BaseTool):
    """Persist a bounded, model-authored navigation prior from description.txt.

    Values recorded here are explicitly *not* source-code facts.  The static
    analysis runtime verifies names and literal hints before exposing them as
    code references or using them as navigation anchors.
    """

    ACCESS_MODES = {"read", "write", "free", "call", "control", "unknown"}
    MEMORY_REGIONS = {"heap", "stack", "global", "container", "unknown"}
    MECHANISM_TAGS = {
        "bounds_read", "bounds_write", "lifetime_use", "lifetime_free",
        "uninitialized_origin", "uninitialized_use", "integer_wrap",
        "negative_length", "null_deref", "type_confusion", "overlap",
        "resource_progress", "format_routing",
    }
    MECHANISM_ALIASES = {
        "bounds": ("bounds_read", "bounds_write"),
        "bound": ("bounds_read", "bounds_write"),
        "bounds_check": ("bounds_read",),
        "bound_check": ("bounds_read",),
        "index": ("bounds_read",),
        "oob": ("bounds_read", "bounds_write"),
        "out_of_bounds": ("bounds_read", "bounds_write"),
        "out-of-bounds": ("bounds_read", "bounds_write"),
        "out_of_bounds_read": ("bounds_read",),
        "oob_read": ("bounds_read",),
        "buffer_overread": ("bounds_read",),
        "buffer_over_read": ("bounds_read",),
        "out_of_bounds_write": ("bounds_write",),
        "oob_write": ("bounds_write",),
        "buffer_overflow": ("bounds_write",),
        "buffer_overrun": ("bounds_write",),
        "heap_buffer_overflow": ("bounds_write",),
        "heap-buffer-overflow": ("bounds_write",),
        "signedness": ("integer_wrap", "negative_length"),
        "signed": ("integer_wrap", "negative_length"),
        "integer_overflow": ("integer_wrap",),
        "int_overflow": ("integer_wrap",),
        "overflow": ("integer_wrap",),
        "underflow": ("integer_wrap",),
        "truncation": ("integer_wrap",),
        "window_truncation": ("integer_wrap",),
        "mask": ("integer_wrap",),
        "negative_size": ("negative_length",),
        "negative_length": ("negative_length",),
        "negative_len": ("negative_length",),
        "negative": ("negative_length",),
        "size_mismatch": ("integer_wrap", "negative_length"),
        "length_mismatch": ("integer_wrap", "negative_length"),
        "size_confusion": ("integer_wrap", "negative_length"),
        "use_after_free": ("lifetime_use",),
        "uaf": ("lifetime_use",),
        "use_after_poison": ("lifetime_use",),
        "free": ("lifetime_free",),
        "double_free": ("lifetime_free",),
        "invalid_free": ("lifetime_free",),
        "uninitialized": ("uninitialized_use",),
        "uninitialized_value": ("uninitialized_use",),
        "uninitialized_read": ("uninitialized_use",),
        "uninitialized_origin": ("uninitialized_origin",),
        "null": ("null_deref",),
        "null_pointer": ("null_deref",),
        "null_deref": ("null_deref",),
        "segv": ("null_deref",),
        "type": ("type_confusion",),
        "bad_cast": ("type_confusion",),
        "type_confusion": ("type_confusion",),
        "function_pointer": ("type_confusion",),
        "overlap": ("overlap",),
        "memcpy_overlap": ("overlap",),
        "format": ("format_routing",),
        "magic": ("format_routing",),
        "dispatch": ("format_routing",),
        "resource": ("resource_progress",),
        "no_progress": ("resource_progress",),
        "hang": ("resource_progress",),
    }
    _LIST_FIELDS = (
        "mechanism_tags", "described_operations", "described_state_transitions",
        "numeric_facts", "suspect_functions", "suspect_files", "suspect_modules",
        "suspect_params", "trigger_conditions", "search_hints",
    )

    def __init__(self) -> None:
        def string_list(description: str) -> Dict[str, Any]:
            return {
                "type": "array", "items": {"type": "string"},
                "description": description,
            }

        super().__init__(ToolSpec(
            name="analyze_description",
            description=(
                "Record a structured interpretation of description.txt. All names and "
                "search hints are unverified priors until the analysis service matches "
                "them to repository code. Prefer concrete operations, transitions, "
                "numbers, identifiers, and trigger conditions over prose."
            ),
            parameters={
                "vuln_type": {"type": "string", "description": "Likely vulnerability class/CWE-style mechanism."},
                "crash_type_hint": {"type": "string", "description": "Likely sanitizer crash type, or UNKNOWN."},
                "access_mode": {"type": "string", "enum": sorted(self.ACCESS_MODES)},
                "memory_region": {"type": "string", "enum": sorted(self.MEMORY_REGIONS)},
                "mechanism_tags": string_list("Normalized mechanism tags such as bounds, signedness, or lifetime."),
                "described_operations": string_list("Operations explicitly described, such as copy, index, resize, free, or cast."),
                "described_state_transitions": string_list("Lifecycle or parser state transitions explicitly described."),
                "numeric_facts": string_list("Exact numeric facts or relationships, preserving units and operators."),
                "suspect_functions": string_list("Function identifiers mentioned or strongly implied by the description."),
                "suspect_files": string_list("File names or paths mentioned by the description."),
                "suspect_modules": string_list("Component, class, namespace, or module names."),
                "suspect_params": string_list("Relevant fields, parameters, flags, lengths, or offsets."),
                "trigger_conditions": string_list("Conditions the input likely must satisfy."),
                "search_hints": string_list("Short literal identifiers or phrases worth verifying in source."),
            },
            required=["vuln_type", "crash_type_hint", "access_mode", "memory_region"],
        ))

    @staticmethod
    def _clean_list(value: Any, limit: int) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = " ".join(str(item or "").split())[:200]
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _normalize_mechanism_tags(cls, value: Any, access_mode: str = "unknown") -> tuple[list[str], list[str]]:
        raw_tags = cls._clean_list(value, 24)
        normalized: list[str] = []
        unknown: list[str] = []
        seen: set[str] = set()
        access = str(access_mode or "unknown").strip().lower()
        for raw in raw_tags:
            key = raw.strip().lower().replace("-", "_").replace(" ", "_")
            mapped = ()
            if key in cls.MECHANISM_TAGS:
                mapped = (key,)
            elif key in cls.MECHANISM_ALIASES:
                mapped = cls.MECHANISM_ALIASES[key]
                if key in {"bounds", "bound", "oob", "out_of_bounds", "out-of-bounds"}:
                    if access == "write":
                        mapped = ("bounds_write",)
                    elif access == "read":
                        mapped = ("bounds_read",)
            else:
                unknown.append(raw)
                continue
            for tag in mapped:
                if tag not in seen:
                    seen.add(tag)
                    normalized.append(tag)
        return normalized[:12], unknown[:12]

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        access_mode = str(args.get("access_mode") or "unknown").strip().lower()
        memory_region = str(args.get("memory_region") or "unknown").strip().lower()
        if access_mode not in self.ACCESS_MODES:
            return ToolValidationResult.fail(f"invalid access_mode: {access_mode}")
        if memory_region not in self.MEMORY_REGIONS:
            return ToolValidationResult.fail(f"invalid memory_region: {memory_region}")
        for field_name in self._LIST_FIELDS:
            value = args.get(field_name, [])
            if value is not None and not isinstance(value, list):
                return ToolValidationResult.fail(f"{field_name} must be an array")
            limit = 24 if field_name == "search_hints" else 12
            if isinstance(value, list) and len(value) > limit:
                return ToolValidationResult.fail(f"{field_name} accepts at most {limit} items")
            if isinstance(value, list) and any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                return ToolValidationResult.fail(f"{field_name} items must be non-empty strings")
        has_content = any(str(args.get(name) or "").strip() for name in ("vuln_type", "crash_type_hint"))
        has_content = has_content or any(args.get(name) for name in self._LIST_FIELDS)
        if not has_content:
            return ToolValidationResult.fail("description analysis cannot be empty")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .analysis.vuln_patterns import normalize_crash_type
        from .state import DescriptionAnalysis

        state = (runtime_context or {}).get("state")
        raw_crash_type = str(args.get("crash_type_hint") or "unknown").strip()
        crash_type = normalize_crash_type(raw_crash_type) or "unknown"
        step = int(getattr(state, "current_step", 0) or 0) if state is not None else 0
        values: Dict[str, Any] = {
            "vuln_type": _clip(args.get("vuln_type"), 120),
            "crash_type_hint": crash_type,
            "access_mode": str(args.get("access_mode") or "unknown").strip().lower(),
            "memory_region": str(args.get("memory_region") or "unknown").strip().lower(),
            "status": "recorded",
            "created_step": step,
            "last_relevant_step": step,
        }
        for field_name in self._LIST_FIELDS:
            values[field_name] = self._clean_list(
                args.get(field_name), 24 if field_name == "search_hints" else 12,
            )
        mechanism_tags, unknown_tags = self._normalize_mechanism_tags(
            args.get("mechanism_tags"), values["access_mode"],
        )
        values["mechanism_tags"] = mechanism_tags
        if unknown_tags:
            merged_hints = values["search_hints"] + [
                f"mechanism:{tag}" for tag in unknown_tags
                if f"mechanism:{tag}" not in values["search_hints"]
            ]
            values["search_hints"] = self._clean_list(merged_hints, 24)
        analysis = DescriptionAnalysis(**values)

        if state is not None:
            from cybergym_agent.agent_impl.core.metadata_keys import (
                CRASH_TYPE_PRIOR,
                CRASH_TYPE_PRIOR_SOURCE,
                DESCRIPTION_ANALYSIS_DIRTY,
                bump_context_revision_value,
            )

            state.description_analysis = analysis
            state.metadata[CRASH_TYPE_PRIOR] = crash_type
            state.metadata[CRASH_TYPE_PRIOR_SOURCE] = "description_analysis"
            state.metadata[DESCRIPTION_ANALYSIS_DIRTY] = True
            bump_context_revision_value(
                state, "description",
                allowed=frozenset({"description", "assessment", "path", "condition", "experiment", "action", "misc"}),
            )

        return {
            "status": "success",
            "analysis_status": "recorded",
            "crash_type_hint": crash_type,
            "suspect_functions": values["suspect_functions"][:6],
            "search_hints": values["search_hints"][:6],
            "normalized_mechanism_tags": values["mechanism_tags"][:6],
            "unmapped_mechanism_tags": unknown_tags[:6],
            "message": "Description priors recorded; source references remain unverified.",
        }


class SetCrashTypeTool(BaseTool):
    """Set the inferred crash type from the vulnerability description.

    The LLM calls this at step 0-1 after reading description.txt to register
    its assessment of the likely ASAN crash type. This feeds into crash-type-
    aware navigation scoring to prioritize the right function patterns.
    After the first submit_poc, the exact crash_type from ASAN output
    overrides this LLM-inferred prior.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="set_crash_type",
                description=(
                    "Set the inferred ASAN crash type based on the vulnerability "
                    "description. Choose one: Heap-buffer-overflow, "
                    "Heap-use-after-free, Heap-double-free, Stack-buffer-overflow, "
                    "Global-buffer-overflow, Use-of-uninitialized-value, "
                    "Index-out-of-bounds, SEGV, or UNKNOWN. "
                    "This helps the static analysis engine prioritize the right "
                    "function patterns for navigation."
                ),
                parameters={
                    "crash_type": {
                        "type": "string",
                        "description": "Inferred crash type (e.g., 'Heap-buffer-overflow')",
                    },
                },
                required=["crash_type"],
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        if not str(args.get("crash_type") or "").strip():
            return ToolValidationResult.fail("crash_type is required")
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .analysis.vuln_patterns import normalize_crash_type

        state = (runtime_context or {}).get("state")
        raw = str(args.get("crash_type") or "").strip()
        normalized = normalize_crash_type(raw)

        if state is not None:
            state.metadata["crash_type_prior"] = normalized
            # Don't overwrite crash_type from ASAN output (that's the ground truth)
            if not getattr(state, "crash_type", ""):
                state.crash_type = normalized

        return {
            "status": "success",
            "crash_type": normalized,
            "original_input": raw,
        }


class ConfirmFormatTool(BaseTool):
    """Confirm or update the detected input format for this task.

    The LLM calls this after identifying the input format from harness source,
    corpus inspection, or other evidence. This activates format-specific
    construction tools, validation, and prompt guidance.
    """

    # Valid format identifiers matching knowledge pack IDs
    _VALID_FORMATS = frozenset({
        "pdf", "sfnt", "packet", "elf", "image", "codec",
        "structured_text", "crypto", "archive", "cad", "audio", "unknown",
    })

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="confirm_format",
                description=(
                    "Confirm or update the detected input format for this task. "
                    "Use after identifying the format from harness source, corpus "
                    "inspection, or hex_view of seed files. This activates "
                    "format-specific construction tools, validation, and guidance. "
                    "Valid formats: pdf, sfnt, packet, elf, image, codec, "
                    "structured_text, crypto, archive, cad, audio. Use 'unknown' "
                    "to reset if the format is truly unclear."
                ),
                parameters={
                    "format_id": {
                        "type": "string",
                        "description": (
                            "Format identifier: pdf, sfnt, packet, elf, image, "
                            "codec, structured_text, crypto, archive, cad, audio, "
                            "or unknown"
                        ),
                    },
                    "confidence": {
                        "type": "string",
                        "description": (
                            "\"confirmed\" if based on strong evidence (corpus magic, "
                            "harness API, fuzzer name). \"candidate\" if based on "
                            "weaker evidence (description keywords, project name)."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "Brief description of what evidence supports this "
                            "format identification"
                        ),
                    },
                },
                required=["format_id"],
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        _ = runtime_context
        fmt = str(args.get("format_id") or "").strip().lower()
        if not fmt:
            return ToolValidationResult.fail("format_id is required")
        if fmt not in self._VALID_FORMATS:
            return ToolValidationResult.fail(
                f"Unknown format_id '{fmt}'. Valid: {', '.join(sorted(self._VALID_FORMATS))}"
            )
        confidence = str(args.get("confidence") or "candidate").strip().lower()
        if confidence not in ("candidate", "confirmed"):
            return ToolValidationResult.fail(
                "confidence must be 'candidate' or 'confirmed'"
            )
        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from .agent_impl.knowledge.evidence import activate_pack_from_tool

        state = (runtime_context or {}).get("state")
        fmt = str(args.get("format_id") or "").strip().lower()
        confidence = str(args.get("confidence") or "candidate").strip().lower()
        evidence_text = str(args.get("evidence") or "").strip()

        if state is None:
            return {"status": "error", "message": "no state available"}

        try:
            updated = activate_pack_from_tool(state, fmt, confidence, evidence_text)
        except Exception as e:
            return {
                "status": "error",
                "message": f"failed to activate format: {e}",
                "format_id": fmt,
            }

        mode = updated.get("mode", "unconfirmed")
        pack_id = updated.get("pack_id", "")
        score = updated.get("detection_score", 0.0)

        result = {
            "status": "success",
            "format_id": fmt,
            "mode": mode,
            "pack_id": pack_id,
            "detection_score": score,
        }

        # Report activated capabilities
        if mode == "confirmed" and pack_id:
            try:
                from .agent_impl.knowledge.registry import get_knowledge_registry
                registry = get_knowledge_registry()
                pack = registry.get_pack(pack_id)
                if pack:
                    result["capabilities"] = sorted(pack.descriptor.capabilities)
                    result["backend_available"] = True
            except Exception:
                pass
            result["message"] = (
                f"Format {pack_id} confirmed. Format-specific validation, "
                f"recipe planning, and build pipeline activated."
            )
        elif mode == "candidate":
            result["message"] = (
                f"Format {pack_id} set as candidate. Use confirm_format "
                f"with confidence='confirmed' after stronger evidence."
            )
        else:
            result["message"] = "Format reset to unknown."

        return result
