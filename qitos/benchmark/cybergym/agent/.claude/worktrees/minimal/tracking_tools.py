"""Lightweight task-local tracking tools for the Minimal CyberGym Agent.

Only two tracking tools: SINK (manage sink candidates) and GATE (manage
path-to-sink constraints). All other tracking tools (record_chain_node,
record_gate, analyze_description, confirm_format, switch_phase) are
consolidated into GATE or removed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult


def _clip(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _get_memory_manager(state: Any):
    """Get MemoryManager from state or agent."""
    # Try direct attribute on state
    mgr = getattr(state, "_memory_mgr", None)
    if mgr is not None:
        return mgr
    # Try via agent reference in runtime_context
    return None


# ------------------------------------------------------------------
# SINK — simplified sink candidate management
# ------------------------------------------------------------------

class SinkTool(BaseTool):
    """Manage sink candidates — functions believed to be the vulnerability crash site.

    Actions:
      add    — Record or upgrade a sink candidate (default)
      retire — Mark as eliminated
      update — Modify confidence/evidence of existing sink
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="SINK",
                description=(
                    "Manage sink candidates — functions you believe are the "
                    "vulnerability crash site. Default action is 'add'. "
                    "Examples: SINK('parse_header', 'attr.c:1880') adds a sink; "
                    "SINK('parse_header', action='retire', reason='wrong location') "
                    "marks it eliminated; "
                    "SINK('parse_header', evidence='unchecked memcpy', confidence=0.7) "
                    "adds with details."
                ),
                parameters={
                    "function": {
                        "type": "string",
                        "description": "Function name (required)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Source location (e.g., 'attribute.c:1880')",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "retire", "update"],
                        "description": "Action: 'add' (default) record or upgrade a sink; 'retire' mark as wrong/eliminated; 'update' modify confidence/evidence of existing sink.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Why this function is a sink (recommended for add, optional for update)",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0.0-1.0. add: suggested 0.7 confirmed, 0.5 strong, 0.4 plausible. update: can raise or lower.",
                    },
                    "callee": {"type": "string", "description": "Optional target callee at the sink callsite"},
                    "expression": {"type": "string", "description": "Optional sink expression"},
                    "category": {"type": "string", "description": "Optional vulnerability/sink category"},
                    "candidate_role": {"type": "string", "description": "Optional role: crash_site | causal_site | path_anchor | dangerous_primitive | unknown"},
                    "paired_with": {"type": "string", "description": "Optional paired candidate/path id for UAF/uninit/integer evidence"},
                    "reason": {"type": "string", "description": "Reason for retire/update action (e.g., 'ASAN crash at different function')"},
                },
                required=["function"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        action = str(args.get("action") or "add").strip()
        if action not in ("add", "retire", "update"):
            return ToolValidationResult.fail("action must be 'add', 'retire', or 'update'")
        if not str(args.get("function") or "").strip():
            return ToolValidationResult.fail("function is required")
        if action == "retire" and not str(args.get("reason") or "").strip():
            return ToolValidationResult.fail("reason is required for retire action")
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
        state = (runtime_context or {}).get("state")
        action = str(args.get("action") or "add").strip()
        func_name = str(args.get("function") or "").strip()

        if action == "add":
            return self._do_add(args, state, func_name)
        if action == "retire":
            return self._do_retire(args, state, func_name)
        if action == "update":
            return self._do_update(args, state, func_name)
        return {"status": "error", "message": f"Unknown action: {action}"}

    def _do_add(self, args, state, func_name):
        from .state import SinkCandidate

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
        paired_with = str(args.get("paired_with") or "").strip()
        file_name, line = "", 0
        raw_file, sep, raw_line = location.rpartition(":")
        if sep and raw_line.isdigit():
            file_name, line = raw_file, int(raw_line)
        elif location:
            file_name = location

        record_action = "created"
        selected = None

        if state is not None:
            live = [c for c in state.sink_candidates if c.status != "eliminated"]
            existing = next((c for c in live if c.function.lower() == func_name.lower()), None)
            if existing is None:
                leaf_matches = [
                    c for c in live
                    if c.function.rsplit("::", 1)[-1].lower() == func_name.rsplit("::", 1)[-1].lower()
                ]
                if len(leaf_matches) == 1:
                    existing = leaf_matches[0]

            if existing is not None:
                record_action = "updated"
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
                existing.source = "model_candidate"
                if confidence > existing.confidence:
                    existing.confidence = confidence
                if evidence and evidence not in (existing.evidence or ""):
                    existing.evidence = (
                        existing.evidence + "; " + evidence
                        if existing.evidence else evidence
                    )
                if location and not existing.location:
                    existing.location = location
                existing.status = "candidate"
                existing.metadata = dict(existing.metadata or {})
                existing.metadata.update({
                    "requires_review": False, "reviewed": True,
                    "confirmed_via": "SINK_tool",
                    "selection_status": "active",
                    "candidate_role": candidate_role,
                    "paired_with": paired_with or existing.metadata.get("paired_with", ""),
                })
                selected = existing
            else:
                new_candidate = SinkCandidate(
                    function=func_name, location=location, confidence=confidence,
                    evidence=evidence, status="candidate", source="model_candidate",
                    file=file_name, line=line, callee=callee, expression=expression,
                    category=category, reason=evidence,
                    metadata={
                        "requires_review": False, "reviewed": True,
                        "confirmed_via": "SINK_tool",
                        "selection_status": "active",
                        "candidate_role": candidate_role,
                        "paired_with": paired_with,
                    },
                )
                if not new_candidate.candidate_id:
                    material = f"{new_candidate.repository_id}|{file_name}|{line}|{func_name}|{callee}|{expression}"
                    new_candidate.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
                state.sink_candidates.append(new_candidate)
                selected = new_candidate

            state.active_sink_id = state._primary_sink_id()
            if not state.active_sink_candidate_id and selected:
                state.active_sink_candidate_id = selected.candidate_id

            # Cap sink candidates
            if len(state.sink_candidates) > 12:
                active = [c for c in state.sink_candidates if c.status != "eliminated"]
                eliminated = [c for c in state.sink_candidates if c.status == "eliminated"]
                state.sink_candidates = active[-12:] + eliminated

            # Update memory files
            self._update_memory(state)

        return {
            "status": "success", "function": func_name,
            "confidence": confidence, "action": record_action,
            "candidate_role": candidate_role,
            "evidence": _clip(evidence, 100),
            "candidate_id": getattr(selected, "candidate_id", "") if selected else "",
        }

    def _do_retire(self, args, state, func_name):
        if state is None:
            return {"status": "error", "message": "No state available"}

        reason = str(args.get("reason") or "").strip()
        live = [c for c in state.sink_candidates if c.status != "eliminated"]
        target = next((c for c in live if c.function.lower() == func_name.lower()), None)

        if target is None:
            return {"status": "error", "message": f"No active sink candidate found for '{func_name}'"}

        target.status = "eliminated"
        target.evidence = (
            (target.evidence + f" [retired: {reason}]")
            if target.evidence else f"Retired: {reason}"
        )
        target.metadata = dict(target.metadata or {})
        target.metadata["retired_reason"] = reason
        target.metadata["selection_status"] = "rejected"

        # Recalculate active sink
        state.active_sink_id = state._primary_sink_id()
        if not state.active_sink_id:
            state.active_sink_candidate_id = ""

        self._update_memory(state)

        return {
            "status": "success", "action": "retired",
            "function": func_name, "reason": reason,
        }

    def _do_update(self, args, state, func_name):
        if state is None:
            return {"status": "error", "message": "No state available"}

        live = [c for c in state.sink_candidates if c.status != "eliminated"]
        target = next((c for c in live if c.function.lower() == func_name.lower()), None)

        if target is None:
            return {"status": "error", "message": f"No active sink candidate found for '{func_name}'"}

        changes = []

        conf = args.get("confidence")
        if conf is not None:
            new_conf = max(0.0, min(1.0, float(conf)))
            old_conf = target.confidence
            target.confidence = new_conf
            changes.append(f"confidence: {old_conf:.2f} -> {new_conf:.2f}")

        for field_name, arg_key in [
            ("callee", "callee"), ("expression", "expression"), ("category", "category"),
        ]:
            val = str(args.get(arg_key) or "").strip()
            if val:
                setattr(target, field_name, val)
                changes.append(f"{arg_key}: {val}")

        candidate_role = str(args.get("candidate_role") or "").strip()
        if candidate_role in {"crash_site", "causal_site", "path_anchor", "dangerous_primitive", "unknown"}:
            target.metadata = dict(target.metadata or {})
            target.metadata["candidate_role"] = candidate_role
            changes.append(f"candidate_role: {candidate_role}")

        evidence = str(args.get("evidence") or "").strip()
        if evidence and evidence not in (target.evidence or ""):
            target.evidence = (target.evidence + "; " + evidence) if target.evidence else evidence
            changes.append("evidence appended")

        state.active_sink_id = state._primary_sink_id()
        self._update_memory(state)

        return {
            "status": "success", "action": "updated",
            "function": func_name, "confidence": target.confidence,
            "changes": changes,
        }

    def _update_memory(self, state):
        """Update sinks.md memory file."""
        mgr = _get_memory_manager(state)
        if mgr is not None:
            try:
                mgr.write_file("sinks.md", mgr.render_sinks_md(state))
                mgr.write_file("MEMORY.md", mgr.render_memory_index(state))
            except Exception:
                pass


# ------------------------------------------------------------------
# GATE — unified constraint/gate management
# ------------------------------------------------------------------

class GateTool(BaseTool):
    """Record and query path-to-sink constraints.

    Consolidates record_chain_node, record_gate, confirm_format, and
    analyze_description into a single tool. The model uses GATE to track
    the call chain from source to sink, record constraints (gates) that
    the PoC must satisfy, and query the current state of the constraint
    model.

    Actions:
      add     — Record a chain node and/or gate (replaces record_chain_node + record_gate)
      query   — List current chain nodes and gates for a sink
      confirm — Mark a gate as confirmed/refuted/questioned
    """

    VALID_GATE_TYPES = frozenset({
        "format_gate", "path_gate", "dispatch_gate", "bounds_gate", "value_gate",
    })
    VALID_ROLES = frozenset({"entry", "parser", "dispatch", "guard", "sink"})
    VALID_STATUSES = frozenset({"confirmed", "inferred", "unknown", "questioned", "refuted"})

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="GATE",
                description=(
                    "Record and query path-to-sink constraints. "
                    "Use GATE to track the call chain from source to sink, record "
                    "constraints that the PoC must satisfy, and confirm or refute them. "
                    "Actions: 'add' (record a chain node or gate), 'query' (list gates "
                    "for a sink), 'confirm' (mark a gate as confirmed/refuted)."
                ),
                parameters={
                    "action": {
                        "type": "string",
                        "enum": ["add", "query", "confirm"],
                        "description": "Action: 'add' record node/gate, 'query' list gates, 'confirm' mark gate status.",
                    },
                    # add parameters
                    "function": {
                        "type": "string",
                        "description": "[add] Function name for chain node or gate.",
                    },
                    "location": {
                        "type": "string",
                        "description": "[add] Source location (e.g., 'attribute.c:1880').",
                    },
                    "role": {
                        "type": "string",
                        "description": "[add] Chain node role: entry, parser, dispatch, guard, sink.",
                    },
                    "description": {
                        "type": "string",
                        "description": "[add] What this node/gate does in the data flow.",
                    },
                    "gate_type": {
                        "type": "string",
                        "description": "[add] Gate type: format_gate, path_gate, dispatch_gate, bounds_gate, value_gate.",
                    },
                    "required_condition": {
                        "type": "string",
                        "description": "[add] Positive condition the PoC must satisfy (e.g., 'APP1 segment starts with Exif magic').",
                    },
                    "status": {
                        "type": "string",
                        "description": "[add/confirm] Status: confirmed, inferred, unknown, questioned, refuted.",
                    },
                    "sink_id": {
                        "type": "string",
                        "description": "Optional sink candidate identifier (function@location). Defaults to primary sink.",
                    },
                    # confirm parameters
                    "gate_description": {
                        "type": "string",
                        "description": "[confirm] Description of the gate to confirm/refute (fuzzy match).",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "[confirm] Evidence supporting the status change.",
                    },
                    "repair_hint": {
                        "type": "string",
                        "description": "[confirm] Suggested fix if gate is refuted.",
                    },
                },
                required=["action"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def validate_input(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> ToolValidationResult:
        action = str(args.get("action") or "").strip()
        if action not in ("add", "query", "confirm"):
            return ToolValidationResult.fail("action must be 'add', 'query', or 'confirm'")

        if action == "add":
            # At least function or gate_type must be provided
            has_node = bool(str(args.get("function") or "").strip())
            has_gate = bool(str(args.get("gate_type") or "").strip())
            if not has_node and not has_gate:
                return ToolValidationResult.fail("add requires at least 'function' (chain node) or 'gate_type' (gate)")
            gate_type = str(args.get("gate_type") or "").strip()
            if gate_type and gate_type not in self.VALID_GATE_TYPES:
                return ToolValidationResult.fail(f"gate_type must be one of: {', '.join(sorted(self.VALID_GATE_TYPES))}")
            role = str(args.get("role") or "").strip()
            if role and role not in self.VALID_ROLES:
                return ToolValidationResult.fail(f"role must be one of: {', '.join(sorted(self.VALID_ROLES))}")

        if action == "confirm":
            if not str(args.get("gate_description") or "").strip():
                return ToolValidationResult.fail("gate_description is required for confirm action")
            status = str(args.get("status") or "").strip()
            if status not in ("confirmed", "refuted", "questioned"):
                return ToolValidationResult.fail("confirm status must be confirmed, refuted, or questioned")

        return ToolValidationResult.ok()

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        state = (runtime_context or {}).get("state")
        action = str(args.get("action") or "").strip()

        if state is None:
            return {"status": "error", "message": "No state available"}

        if action == "add":
            return self._do_add(args, state)
        if action == "query":
            return self._do_query(args, state)
        if action == "confirm":
            return self._do_confirm(args, state)

        return {"status": "error", "message": f"Unknown action: {action}"}

    def _do_add(self, args, state):
        from .state import ChainNode, ChainGate

        function = str(args.get("function") or "").strip()
        location = str(args.get("location") or "").strip()
        role = str(args.get("role") or "parser").strip()
        description = str(args.get("description") or "").strip()
        gate_type = str(args.get("gate_type") or "").strip()
        required_condition = str(args.get("required_condition") or "").strip()
        status = str(args.get("status") or "inferred").strip()
        sink_id = str(args.get("sink_id") or "").strip()
        if not sink_id:
            sink_id = state._primary_sink_id()

        result_parts = []

        # Add chain node if function provided
        if function:
            existing_keys = {f"{n.function}@{n.location}@{n.sink_id}" for n in state.call_chain_nodes}
            key = f"{function}@{location}@{sink_id}"
            if key in existing_keys:
                for n in state.call_chain_nodes:
                    if f"{n.function}@{n.location}@{n.sink_id}" == key:
                        n.role = role
                        n.description = description
                        n.status = status
                        break
                result_parts.append(f"updated node {function}@{location}")
            else:
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
                    evidence="GATE tool by agent",
                    order=max_order + 1,
                    sink_id=sink_id,
                ))
                result_parts.append(f"added node {function}@{location}")
            # Cap
            if len(state.call_chain_nodes) > 20:
                state.call_chain_nodes = state.call_chain_nodes[-20:]

        # Add gate if gate_type provided
        if gate_type:
            node_order = 0
            if function:
                for n in state.call_chain_nodes:
                    if n.function == function and (
                        n.sink_id == sink_id
                        or (not n.sink_id and sink_id == state._primary_sink_id())
                        or not sink_id
                    ):
                        node_order = n.order
                        break

            gate_desc = description or f"{gate_type} on {function}"
            gate_cond = required_condition or "TBD"

            existing = None
            for g in state.call_chain_gates:
                if g.description == gate_desc:
                    existing = g
                    break

            if existing is not None:
                existing.gate_type = gate_type
                existing.required_condition = gate_cond
                existing.status = status
                existing.node_order = node_order
                result_parts.append(f"updated gate '{_clip(gate_desc, 60)}'")
            else:
                state.call_chain_gates.append(ChainGate(
                    node_order=node_order,
                    gate_type=gate_type,
                    description=gate_desc,
                    required_condition=gate_cond,
                    status=status,
                    evidence="GATE tool by agent",
                    repair_hint="",
                    sink_id=sink_id,
                ))
                result_parts.append(f"added gate '{_clip(gate_desc, 60)}'")
            # Cap
            if len(state.call_chain_gates) > 40:
                state.call_chain_gates = state.call_chain_gates[-40:]

        state.gate_board_last_changed_step = int(getattr(state, "current_step", 0) or 0)
        self._update_memory(state)

        return {
            "status": "success",
            "action": "add",
            "details": "; ".join(result_parts) if result_parts else "no changes",
            "sink_id": sink_id,
        }

    def _do_query(self, args, state):
        sink_id = str(args.get("sink_id") or "").strip()
        if not sink_id:
            sink_id = state._primary_sink_id()

        nodes = state.nodes_for_sink(sink_id)
        gates = state.gates_for_sink(sink_id)

        chain_lines = []
        for n in nodes:
            chain_lines.append(
                f"  {n.order}. [{n.status}] {n.role}: {n.function} @ {n.location}"
                + (f" — {n.description}" if n.description else "")
            )

        gate_lines = []
        for g in gates:
            icon = {"confirmed": "+", "refuted": "X", "inferred": "?"}.get(g.status, "?")
            gate_lines.append(
                f"  [{icon}] {g.gate_type}: {g.description}"
                + (f"\n     required: {g.required_condition}" if g.required_condition else "")
            )

        return {
            "status": "success",
            "action": "query",
            "sink_id": sink_id or "(no active sink)",
            "chain": chain_lines,
            "gates": gate_lines,
            "open_gates": len(state.open_gates()),
            "confirmed_gates": len(state.confirmed_gates()),
        }

    def _do_confirm(self, args, state):
        gate_desc = str(args.get("gate_description") or "").strip().lower()
        new_status = str(args.get("status") or "").strip()
        evidence = str(args.get("evidence") or "").strip()
        repair_hint = str(args.get("repair_hint") or "").strip()

        # Find matching gate (fuzzy match on description)
        target = None
        for g in state.call_chain_gates:
            if gate_desc in g.description.lower() or g.description.lower() in gate_desc:
                target = g
                break

        if target is None:
            return {"status": "error", "message": f"No gate found matching '{gate_desc}'"}

        old_status = target.status
        target.status = new_status
        if evidence:
            target.evidence = (target.evidence + "; " + evidence) if target.evidence else evidence
        if repair_hint:
            target.repair_hint = repair_hint

        state.gate_board_last_changed_step = int(getattr(state, "current_step", 0) or 0)
        self._update_memory(state)

        return {
            "status": "success",
            "action": "confirm",
            "gate": _clip(target.description, 80),
            "old_status": old_status,
            "new_status": new_status,
        }

    def _update_memory(self, state):
        """Update gates.md memory file."""
        mgr = _get_memory_manager(state)
        if mgr is not None:
            try:
                mgr.write_file("gates.md", mgr.render_gates_md(state))
                mgr.write_file("MEMORY.md", mgr.render_memory_index(state))
            except Exception:
                pass


# Backward compat alias
RecordSinkCandidateTool = SinkTool
