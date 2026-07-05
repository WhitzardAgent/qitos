"""Memory and state display for observation rendering — extracted from renderer.py."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...context import PROJECT_ARTIFACT_ROOT
from ...state import CyberGymState
from ..core.constants import (
    SUGGESTED_CONSTRAINTS_ENABLED,
)
from .validation import ValidationMixin


# --- Module-level gate utilities ---

def _detect_gate_contradictions(gates, suggestions=None) -> List[str]:
    """Detect logical contradictions between chain gates and suggestions.

    Uses simple heuristic patterns to find cases where one gate
    requires a condition that another gate makes impossible.
    Returns a list of human-readable contradiction descriptions.
    """
    import re as _re
    results: List[str] = []
    active_gates = [g for g in gates if g.status in ("confirmed", "inferred", "questioned")]
    if len(active_gates) < 2 and not suggestions:
        return results

    # Build variable -> condition maps for path_gate and value_gate
    var_assignments: Dict[str, List[str]] = {}  # var -> [gate_desc, ...]
    var_nonnull: Dict[str, List[str]] = {}  # var -> [gate_desc, ...]
    # reachability bounds for trigger vs reachability cross-check
    reachability_bounds: Dict[str, List[str]] = {}  # var -> [cond, ...]

    for g in active_gates:
        cond = str(g.required_condition or "")
        desc = str(g.description or "")
        # Pattern: "copy must be false" or "copy=false" in path_gate
        if g.gate_type == "path_gate":
            m = _re.search(r'(\w+)\s*(?:must be|=)\s*(false|true|0|1|null|NULL|nullptr)', cond, _re.IGNORECASE)
            if m:
                var_name = m.group(1).lower()
                value = m.group(2).lower()
                var_assignments.setdefault(var_name, []).append(f"{desc} ({var_name}={value})")

        # Pattern: "data != NULL" or "data must be non-zero/non-null" in value_gate/bounds_gate
        if g.gate_type in ("value_gate", "bounds_gate"):
            m = _re.search(r'(\w+)\s*!=\s*(?:NULL|nullptr|null|0)', cond, _re.IGNORECASE)
            if m:
                var_name = m.group(1).lower()
                var_nonnull.setdefault(var_name, []).append(desc)
            m2 = _re.search(r'(\w+)\s+must be\s+(?:non-zero|non-null|true|positive)', cond, _re.IGNORECASE)
            if m2:
                var_name = m2.group(1).lower()
                var_nonnull.setdefault(var_name, []).append(desc)
            # Collect reachability bounds for trigger cross-check
            if getattr(g, "role", "reachability") == "reachability":
                # Extract: "var < N" or "var >= N" etc.
                bm = _re.search(r'(\w+)\s*([<>=!]+)\s*(\d+|0x[\da-fA-F]+)', cond)
                if bm:
                    var_name = bm.group(1).lower()
                    reachability_bounds.setdefault(var_name, []).append(
                        f"{desc} ({bm.group(0)})"
                    )

    # Check: variable assigned false/null/0 AND required non-null
    for var in set(var_assignments) & set(var_nonnull):
        for assignment in var_assignments[var]:
            for nonnull_desc in var_nonnull[var]:
                results.append(
                    f"Variable '{var}': {assignment} contradicts "
                    f"{nonnull_desc} (requires {var} to be non-null)"
                )
                if len(results) >= 3:
                    return results

    # Check: trigger violation_formula vs reachability bounds
    if suggestions:
        for s in suggestions:
            if s.get("role") != "trigger":
                continue
            violation = str(s.get("violation_formula", "") or "").strip()
            if not violation:
                continue
            # Extract variable and comparison from violation
            vm = _re.search(r'(\w+)\s*([<>=!]+)\s*(\d+|0x[\da-fA-F]+)', violation)
            if not vm:
                continue
            var_name = vm.group(1).lower()
            if var_name in reachability_bounds:
                for reach_desc in reachability_bounds[var_name]:
                    results.append(
                        f"Trigger vs reachability on '{var_name}': "
                        f"reachability says {reach_desc}, but trigger violation says {violation}"
                    )
                    if len(results) >= 3:
                        return results

        # Check: dataflow == binding vs value_gate != requirement
        for s in suggestions:
            if s.get("role") != "dataflow":
                continue
            formula = str(s.get("normalized_formula", "") or "")
            dm = _re.search(r'(\w+)\s*==\s*(\d+|0x[\da-fA-F]+|nullptr|null|NULL)', formula)
            if not dm:
                continue
            var_name = dm.group(1).lower()
            bound_val = dm.group(2)
            # Check if any value_gate requires var != same value
            for g in active_gates:
                if g.gate_type != "value_gate":
                    continue
                cond = str(g.required_condition or "")
                nm = _re.search(
                    rf'{_re.escape(var_name)}\s*!=\s*({_re.escape(bound_val)})',
                    cond, _re.IGNORECASE,
                )
                if nm:
                    results.append(
                        f"Dataflow vs value gate on '{var_name}': "
                        f"dataflow binds {var_name} == {bound_val}, "
                        f"but value_gate requires {cond}"
                    )
                    if len(results) >= 3:
                        return results

    return results


def _gate_to_instruction(gate) -> str:
    """Convert a ChainGate into a natural language PoC instruction.

    Uses the gate's internal type only for routing to the right
    instruction template — the LLM never sees the type label.
    """
    desc = str(gate.description or "")
    cond = str(gate.required_condition or "")

    if gate.gate_type == "format_gate":
        if cond:
            return f"Input must satisfy: {cond}"
        return desc

    if gate.gate_type == "bounds_gate":
        if cond:
            return f"Set field values so that: {cond}"
        return desc

    if gate.gate_type == "dispatch_gate":
        if cond:
            return f"Route input through: {cond}"
        return desc

    if gate.gate_type == "path_gate":
        if cond:
            return f"Satisfy branch condition: {cond}"
        return desc

    if gate.gate_type == "value_gate":
        if cond:
            return f"Set specific value: {cond}"
        return desc

    return desc if desc else cond


def _build_blueprint(state, confirmed_gates, _re) -> List[str]:
    """Build a byte-level PoC layout from gate conditions + attempt history.

    Parses hex byte sequences from format_gate required_conditions and
    lays them out in an offset table. Falls back to field_specs when
    no concrete hex bytes are available.
    """
    lines: List[str] = []

    hex_specs = []   # (hex_string, purpose)
    field_specs = []  # (description, condition_text)

    for g in confirmed_gates:
        cond = str(g.required_condition or "")
        desc = str(g.description or "")

        if g.gate_type == "format_gate":
            # Pattern 1: space-separated hex bytes without 0x prefix
            # e.g., "Profile data starts with 45 78 69 66 00 00"
            m = _re.search(r'([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})+)', cond)
            if m:
                hex_specs.append((m.group(1), desc))
            # Pattern 2: 0x-prefixed hex bytes
            elif _re.search(r'0x[0-9A-Fa-f]{2}', cond):
                hex_bytes = _re.findall(r'0x([0-9A-Fa-f]{2})', cond)
                if hex_bytes:
                    hex_specs.append((" ".join(hex_bytes), desc))

        elif g.gate_type in ("bounds_gate", "value_gate", "path_gate", "dispatch_gate"):
            if cond:
                field_specs.append((desc, cond))

    if not hex_specs and not field_specs:
        return lines

    lines.append("```")
    lines.append("Offset   Bytes              Purpose")
    lines.append("------   -----              -------")

    offset = 0
    for hex_str, purpose in hex_specs:
        byte_count = len(hex_str.split())
        short_purpose = purpose.split(".")[0] if purpose else ""
        lines.append(f"0x{offset:04X}   {hex_str:<19s}{short_purpose}")
        offset += byte_count

    if field_specs:
        if hex_specs:
            lines.append("------   Fixed bytes above; variable fields below ------")
        for desc, cond in field_specs:
            short_desc = desc.split(".")[0] if desc else "Field constraint"
            lines.append(f"         {short_desc}")
            lines.append(f"           Condition: {cond}")

    lines.append("```")

    # Exploit status from attempt history
    if state.vul_crashed():
        crash_info = []
        if state.crash_type:
            crash_info.append(state.crash_type)
        if state.crash_location:
            crash_info.append(f"at {state.crash_location}")
        if crash_info:
            lines.append(f"Working trigger: {', '.join(crash_info)}")
        hyp = str(state.current_hypothesis or "").strip()
        if hyp and "VUL-ONLY" in hyp:
            lines.append("Next step: reduce overflow to minimal bytes for precision")

    return lines


class MemoryMixin:
    """Mixin providing memory/state display methods for observation rendering."""

    # ------------------------------------------------------------------
    # Static display helpers (TUI / compact text)
    # ------------------------------------------------------------------

    @staticmethod
    def _sink_candidates_text(state: CyberGymState) -> str:
        """Render Sink Candidates section text for TUI display.

        This is the exact same text the LLM sees in the observation packet,
        including the instructional nudge when no candidates have been recorded.
        """
        sink_candidates = [c for c in (getattr(state, "sink_candidates", None) or [])
                           if c.status != "eliminated"]
        lines: List[str] = []
        if sink_candidates:
            lines.append(f"Sink Candidates ({len(sink_candidates)}):")
            for c in sorted(sink_candidates, key=lambda x: -x.confidence)[:5]:
                from ...analysis.vuln_patterns import is_entry_point_function
                is_entry = is_entry_point_function(c.function)
                conf_label = "entry" if is_entry else "high" if c.confidence >= 0.7 else "medium" if c.confidence >= 0.4 else "low"
                status = f" [{c.status}]" if c.status != "candidate" else ""
                if is_entry:
                    status += " [ENTRY — NOT CRASH SITE]"
                meta = c.metadata or {}
                tags = []
                if meta.get("graph_validated"):
                    tags.append("graph-validated")
                if meta.get("reachable_from_entry"):
                    tags.append("reachable")
                if meta.get("description_anchor_stale"):
                    tags.append("STALE")
                risk_count = int(meta.get("risk_signal_count", 0) or 0)
                if risk_count > 0:
                    tags.append(f"{risk_count} risk{'s' if risk_count != 1 else ''}")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                if bool(meta.get("requires_review")):
                    label = "STATIC LEAD" if c.source in {"static_navigation", "graph_auto_deepen"} else "WEAK PRIOR"
                    status += f" [{label}—REQUIRES MODEL CONFIRMATION]"
                auto_prefix = "[AUTO] " if c.source in {"static_navigation", "graph_auto_deepen"} else ""
                lines.append(f"- {auto_prefix}`{c.function}` ({conf_label} conf){status}{tag_str} — {c.evidence}")
        else:
            checkpoint_active = getattr(state, "pending_sink_checkpoint", False)
            if checkpoint_active:
                lines.append("SINK HYPOTHESIS NEEDED — record_sink_candidate() is recommended. "
                             "You may proceed with other actions if you have a working hypothesis.")
            else:
                lines.append("No sink candidates recorded. Call record_sink_candidate() "
                             "when you identify a vulnerable function. REQUIRED before leaving exploration.")
        return "\n".join(lines)

    @staticmethod
    def _task_context_text(state: CyberGymState) -> str:
        """Render Task Context section text for TUI display."""
        lines: List[str] = []
        if state.vulnerability_description:
            desc_text = state.vulnerability_description.replace("\n", " ")
            lines.append(f"Vulnerability: {desc_text}")
        if state.bug_type:
            lines.append(f"Bug Type: {state.bug_type}")
        crash_type_prior = str((state.metadata or {}).get("crash_type_prior", "") or getattr(state, "crash_type", "") or "")
        if crash_type_prior:
            lines.append(f"Crash Type: {crash_type_prior}")
        if state.poc_strategy:
            lines.append(f"Strategy: {state.poc_strategy}")
        if hasattr(state, "input_format") and state.input_format and state.input_format.format_type:
            fmt = state.input_format
            fmt_line = f"Input Format: {fmt.format_type}"
            if fmt.entry_point:
                status = "confirmed" if fmt.confirmed else "inferred"
                fmt_line += f" | Entry: {fmt.entry_point} ({status})"
            if fmt.input_path:
                fmt_line += f" | Input via: {fmt.input_path}"
            if fmt.magic_bytes:
                fmt_line += f" | Magic: {fmt.magic_bytes}"
            lines.append(fmt_line)
        if state.harness_entry_confirmed or (isinstance(getattr(state, "metadata", None), dict)
                                              and state.metadata.get("harness_entry_confirmed")):
            lines.append("Harness entry: confirmed (LLVMFuzzerTestOneInput found)")
        return "\n".join(lines)

    @staticmethod
    def _suggested_sinks_text(state: CyberGymState) -> str:
        """Render Suggested Sinks section text for TUI display.

        Auto-discovered sinks not yet confirmed by the model.
        """
        auto_sources = {"static_navigation", "graph_auto_deepen"}
        model_confirmed = {c.function.lower() for c in (getattr(state, "sink_candidates", []) or [])
                           if c.source == "model_candidate" and c.status != "eliminated"}
        unconfirmed_auto = [c for c in (getattr(state, "sink_candidates", []) or [])
                            if c.source in auto_sources
                            and c.status != "eliminated"
                            and c.function.lower() not in model_confirmed
                            and c.confidence >= 0.5]
        if not unconfirmed_auto:
            return ""
        lines: List[str] = []
        for c in sorted(unconfirmed_auto, key=lambda x: -x.confidence)[:3]:
            meta = c.metadata or {}
            role = meta.get("role", "")
            risk_signals = meta.get("risk_signals") or [{}]
            risk_desc = risk_signals[0].get("reason", "") if risk_signals else ""
            detail = f" ({role})" if role else ""
            if risk_desc:
                detail += f" — {risk_desc}"
            lines.append(f"[AUTO] {c.function}{detail}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Instance methods (need self for cross-mixin calls)
    # ------------------------------------------------------------------

    def _working_memory_lines(self, state: CyberGymState) -> List[str]:
        lines: List[str] = []
        # Code facts (from READ hits on parser/field/seed paths)
        # No truncation — first entry into LLM context must be complete.
        code_facts = list(state.durable_code_facts or [])
        if code_facts:
            lines.append("### Code Facts")
            for fact in code_facts:
                if fact.startswith("[confirmed]"):
                    lines.append(f"- [confirmed] {fact[12:]}")
                elif fact.startswith("[inferred]"):
                    lines.append(f"- [inferred] {fact[11:]}")
                else:
                    lines.append(f"- {fact}")
        # Feedback facts (from submit results)
        # No truncation — first entry into LLM context must be complete.
        fb_facts = list(state.durable_feedback_facts or [])
        if fb_facts:
            lines.append("### Feedback Facts")
            for fact in fb_facts:
                lines.append(f"- {fact}")
        # Active constraints extracted from feedback
        constraints = self._extract_constraints_from_facts(state)
        if constraints:
            lines.append("### Active Constraints")
            for c in constraints:
                lines.append(f"- {c}")
        # Read coverage — which files have been read
        if state.read_coverage:
            cov_lines = ["### Read Coverage"]
            for path, ranges in list(state.read_coverage.items()):
                range_str = ", ".join(f"L{a}-{b}" for a, b in ranges[-3:])
                cov_lines.append(f"- `{path}`: {range_str}")
            lines.extend(cov_lines)
        return lines

    def _extract_constraints_from_facts(self, state: CyberGymState) -> List[str]:
        constraints: List[str] = []
        seen: set[str] = set()
        for fact in list(state.durable_feedback_facts or []):
            if fact.startswith("failed_gate:"):
                gate = fact.split(":", 1)[1].strip()
                entry = f"Gate: {gate}"
                if entry not in seen:
                    seen.add(entry)
                    constraints.append(entry)
            elif fact.startswith("crash_type:"):
                ct = fact.split(":", 1)[1].strip()
                entry = f"Crash type: {ct}"
                if entry not in seen:
                    seen.add(entry)
                    constraints.append(entry)
            elif fact.startswith("crash_location:"):
                cl = fact.split(":", 1)[1].strip()
                entry = f"Crash location: {cl}"
                if entry not in seen:
                    seen.add(entry)
                    constraints.append(entry)
        return constraints

    def _project_memory_lines(self, state: CyberGymState) -> List[str]:
        memory = dict(state.durable_project_memory or {})
        lines: List[str] = []
        repo_summary = str(memory.get("repo_summary") or "").strip()
        if repo_summary:
            lines.append(f"- Repo Summary: {repo_summary}")
        for label, key in (
            ("Parser Paths", "parser_paths"),
            ("Seed Paths", "seed_paths"),
            ("Field Paths", "field_paths"),
        ):
            values = [str(item).strip() for item in list(memory.get(key) or []) if str(item).strip()]
            if values:
                rendered = ", ".join(f"`{value}`" for value in values)
                lines.append(f"- {label}: {rendered}")
        return lines

    # ------------------------------------------------------------------
    # Static methods (constraint board, memory lines, state display)
    # ------------------------------------------------------------------

    @staticmethod
    def _constraint_board_lines(state: CyberGymState) -> List[str]:
        """Render vulnerability context as a PoC Construction Blueprint.

        Three narrative sections that read like a security researcher's notes,
        using natural language instead of internal gate-type taxonomy.
        Falls back to legacy path_constraints when no chain data exists.
        """
        import re as _re
        lines: List[str] = []
        # Harness signals (kept as-is, already natural language)
        signals = list(getattr(state, "harness_signals", []) or [])[-6:]
        if signals:
            rendered = []
            for item in signals:
                name = str(getattr(item, "name", "") or "").strip()
                source = str(getattr(item, "source", "") or "").strip()
                if name and source:
                    rendered.append(f"`{name}` @ `{source}`")
                elif name:
                    rendered.append(f"`{name}`")
            if rendered:
                lines.append("- Harness Signals: " + ", ".join(rendered))

        # --- CallChain rendering ---
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        gates = list(getattr(state, "call_chain_gates", []) or [])

        if nodes or gates:
            # -- Multi-sink summary --
            active_sinks = state.confirmed_sink_candidates()
            active_sink_id = getattr(state, "active_sink_id", "") or ""

            if active_sinks:
                lines.append("### Active sink candidates")
                for c in sorted(active_sinks, key=lambda x: -x.confidence):
                    sid = f"{c.function}@{c.location}"
                    conf_label = "high" if c.confidence >= 0.7 else "medium" if c.confidence >= 0.4 else "low"
                    marker = " < ACTIVE" if sid == active_sink_id else ""
                    status_tag = f" [{c.status}]" if c.status != "candidate" else ""
                    lines.append(f"- `{c.function}` ({conf_label} conf){status_tag}{marker}")
                lines.append("")

            # Filter to active sink's nodes/gates when multi-sink
            if active_sink_id and len(active_sinks) > 1:
                primary = state._primary_sink_id()
                nodes = [n for n in nodes
                         if n.sink_id == active_sink_id
                         or (not n.sink_id and active_sink_id == primary)]
                gates = [g for g in gates
                         if g.sink_id == active_sink_id
                         or (not g.sink_id and active_sink_id == primary)]

            confirmed_g = [g for g in gates if g.status == "confirmed"]
            open_g = [g for g in gates if g.status in ("inferred", "unknown", "questioned")]
            refuted_g = [g for g in gates if g.status == "refuted"]
            questioned_g = [g for g in gates if g.status == "questioned"]

            # Detect contradictions between gates and suggestions
            suggestions_for_contra = list(getattr(state, "suggested_constraints", []) or [])
            contradictions = _detect_gate_contradictions(gates, suggestions_for_contra)

            # -- Vulnerability path summary --
            if nodes:
                sorted_nodes = sorted(nodes, key=lambda n: n.order)
                chain_names = " -> ".join(n.function for n in sorted_nodes)
                lines.append("### Vulnerability path")
                sink = next(
                    (n for n in sorted_nodes if n.role == "sink"),
                    sorted_nodes[-1],
                )
                if sink.description:
                    lines.append(sink.description)
                lines.append(f"Call path: {chain_names}")
                lines.append("")

            # -- Confirmed requirements --
            if confirmed_g:
                lines.append("### Confirmed requirements")
                evidence_brief = getattr(state, "gate_evidence_brief", {}) or {}
                for g in confirmed_g:
                    instruction = _gate_to_instruction(g)
                    brief = evidence_brief.get(g.description, "")
                    span = getattr(g, "source_span", {}) or {}
                    loc = f" (line {span['start_line']})" if span.get("start_line") else ""
                    if brief:
                        lines.append(f"- {instruction} (evidence: {brief}){loc}")
                    else:
                        lines.append(f"- {instruction}{loc}")
                lines.append("")

            # -- Concrete input layout hints --
            blueprint = _build_blueprint(state, confirmed_g, _re)
            if blueprint:
                lines.append("### Concrete input layout")
                lines.extend(blueprint)
                lines.append("")

            # -- Failed approaches --
            if refuted_g:
                lines.append("### Failed approaches")
                for g in refuted_g[-5:]:
                    desc = g.description
                    span = getattr(g, "source_span", {}) or {}
                    if span.get("start_line"):
                        desc += f" [line {span['start_line']}]"
                    if g.repair_hint:
                        desc += f" -> {g.repair_hint}"
                    lines.append(f"- {desc}")
                lines.append("")

            # -- Questioned gates --
            if questioned_g:
                lines.append("### Questioned gates (may be correct — confirm or adjust)")
                for g in questioned_g[-5:]:
                    desc = g.description
                    span = getattr(g, "source_span", {}) or {}
                    if span.get("start_line"):
                        desc += f" [line {span['start_line']}]"
                    if g.repair_hint:
                        desc += f" -> {g.repair_hint}"
                    lines.append(f"- {desc}")
                lines.append("")

            # -- Constraint coverage --
            if nodes:
                sorted_nodes = sorted(nodes, key=lambda n: n.order)
                uncovered = []
                coverage_lines = []
                for node in sorted_nodes:
                    node_gates = [g for g in gates if g.node_order == node.order]
                    confirmed_count = sum(1 for g in node_gates if g.status == "confirmed")
                    total_count = len(node_gates)
                    loc_short = node.location.split("/")[-1] if "/" in node.location else node.location
                    if confirmed_count > 0:
                        coverage_lines.append(
                            f"  [{node.role}] {node.function} @ {loc_short} — "
                            f"{confirmed_count}/{total_count} gate(s) confirmed"
                        )
                    elif total_count > 0:
                        coverage_lines.append(
                            f"  [{node.role}] {node.function} @ {loc_short} — "
                            f"0/{total_count} gate(s) confirmed (all inferred)"
                        )
                        uncovered.append(node)
                    else:
                        stale_steps = (
                            (getattr(state, "current_step", 0) or 0)
                            - getattr(state, "gate_board_last_changed_step", 0)
                        )
                        stale_msg = ""
                        if stale_steps > 20:
                            stale_msg = f" (board stale for {stale_steps} steps! Use record_gate NOW)"
                        elif stale_steps > 10:
                            stale_msg = f" (board stale for {stale_steps} steps)"
                        coverage_lines.append(
                            f"  [{node.role}] {node.function} @ {loc_short} — "
                            f"NO gates discovered{stale_msg}"
                        )
                        uncovered.append(node)

                if coverage_lines:
                    lines.append("### Constraint coverage")
                    lines.extend(coverage_lines)
                    if uncovered:
                        names = [n.function for n in uncovered[:3]]
                        lines.append(
                            f"WARNING: Nodes with no confirmed constraints: {', '.join(names)}. "
                            "READ their code to discover hidden conditions before constructing PoC."
                        )
                    lines.append("")

            # -- Contradiction detection --
            if contradictions:
                lines.append("### Contradiction detected")
                for c in contradictions[:3]:
                    lines.append(f"- {c}")
                lines.append("")

            # -- Interprocedural analysis --
            brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
            if brief and brief.get("status") in ("success", "partial"):
                paths = brief.get("candidate_paths", [])
                requirements = brief.get("requirements") or brief.get("key_constraints") or []
                gaps = brief.get("gaps") or []
                target = brief.get("candidate") or {}
                target_name = str(target.get("function") or "unknown")
                # Build informative path summary
                path_summaries = []
                for p in paths[:2]:
                    chain = str(p.get("chain_details") or p.get("chain") or "")
                    if chain:
                        path_summaries.append(chain[:120])
                path_info = "; ".join(path_summaries) if path_summaries else f"{len(paths)} path(s)"
                lines.append(
                    f"- Sink Analysis: `{target_name}` | {brief.get('status')} | "
                    f"path: {path_info}"
                )
                for req in requirements[:2]:
                    expr = str(req.get("expression") or "")[:80]
                    if expr:
                        lines.append(f"  req: {expr}")
                for gap in gaps[:2]:
                    reason = str(gap.get("reason") or "")
                    if reason:
                        lines.append(f"  gap: {reason}")
                lines.append("")
            elif getattr(state, "active_sink_candidate_id", ""):
                # Show active sink even without analysis brief
                active_sinks = state.confirmed_sink_candidates()
                if active_sinks:
                    best = max(active_sinks, key=lambda c: c.confidence)
                    loc = str(best.location or best.file or "")
                    lines.append(f"- Active Sink: `{best.function}` ({loc}) — analysis pending")
                    lines.append("")

            # -- Suggested constraints (auto-extracted, LLM judges) --
            if SUGGESTED_CONSTRAINTS_ENABLED:
                suggestions = list(getattr(state, "suggested_constraints", []) or [])
                # Only show satisfy-polarity suggestions (avoid-exit ones are
                # noisy and the tree-sitter extractor already marks them).
                satisfy_suggestions = [
                    s for s in suggestions if s.get("polarity", "satisfy") == "satisfy"
                ]
                if satisfy_suggestions:
                    lines.append("### Suggested constraints")
                    lines.append(
                        "These are grouped, source-backed analyzer candidates. Only "
                        "reachability conditions may be ordinary path gates; trigger and "
                        "hazard findings require source review before record_gate."
                    )
                    last_group = None
                    for s in satisfy_suggestions[-12:]:
                        gtype = s.get("gate_type", "unknown")
                        desc = s.get("description", "")
                        cond = s.get("required_condition", "")
                        role = s.get("role", "reachability")
                        path_id = s.get("path_id", "unassigned")
                        confidence = s.get("confidence", "unknown")
                        group = (path_id, role)
                        if group != last_group:
                            lines.append(f"### Path `{path_id}` . role `{role}`")
                            last_group = group
                        # Show gate type as natural language label
                        type_label = {
                            "bounds_gate": "BOUNDS",
                            "dispatch_gate": "DISPATCH",
                            "path_gate": "GUARD",
                            "format_gate": "FORMAT",
                            "value_gate": "VALUE",
                        }.get(gtype, gtype)
                        lines.append(f"- [{confidence.upper()}] [{type_label}] {desc}")
                        if cond:
                            lines.append(f"  Condition: {cond}")
                        safe = s.get("safe_formula", "")
                        if safe:
                            lines.append(f"  Safe invariant: {safe}")
                    lines.append("")

                # Analyzer diagnostics — both feedback and analyzer sources
                diagnostics = list(getattr(state, "constraint_diagnostics", []) or [])[-5:]
                if diagnostics:
                    lines.append("### Analyzer diagnostics")
                    for item in diagnostics[-5:]:
                        source_tag = "[FEEDBACK]" if item.get("source") == "feedback" else "[ANALYZER]"
                        lines.append(
                            f"- {source_tag} [{str(item.get('severity', 'info')).upper()}] "
                            f"{item.get('code', 'analysis')}: {item.get('message', '')}"
                        )
                    lines.append("")

            # -- Unresolved questions --
            if open_g:
                lines.append("### Unresolved questions")
                for g in open_g:
                    lines.append(f"- {g.description}")
                    if g.required_condition:
                        lines.append(f"  Need to confirm: {g.required_condition}")
        else:
            # Fallback: legacy path_constraints
            constraints = list(getattr(state, "path_constraints", []) or [])
            if constraints:
                confirmed = [item for item in constraints if str(getattr(item, "status", "") or "").lower() == "confirmed"]
                open_items = [
                    item
                    for item in constraints
                    if str(getattr(item, "status", "") or "").lower() != "confirmed"
                ]
                lines.append(f"- Path Constraints: {len(confirmed)} confirmed / {len(open_items)} open")
                for item in open_items[-8:]:
                    desc = str(getattr(item, "description", "") or "")
                    source = str(getattr(item, "source_location", "") or "").strip()
                    status = str(getattr(item, "status", "") or "unknown")
                    if source:
                        lines.append(f"- [{status}] {desc} (`{source}`)")
                    else:
                        lines.append(f"- [{status}] {desc}")
        return lines

    @staticmethod
    def _task_memory_lines(state: CyberGymState) -> List[str]:
        """Render task-persistent memory — full detail, survives context compaction."""
        lines: List[str] = []
        # Vulnerability analysis
        va = str(getattr(state, "vulnerability_analysis", "") or "").strip()
        if va:
            lines.append(f"- Analysis: {va}")
        # Path trace
        pt = list(getattr(state, "path_trace", []) or [])
        if pt:
            for entry in pt[-8:]:
                lines.append(f"- Path: {entry}")
        # Attempt history compact
        ah = list(getattr(state, "attempt_history_compact", []) or [])
        if ah:
            for entry in ah[-8:]:
                lines.append(f"- Attempt: {entry}")
        # Current hypothesis
        ch = str(getattr(state, "current_hypothesis", "") or "").strip()
        if ch:
            lines.append(f"- Hypothesis: {ch}")
        # Gate evidence briefs — survive context compaction
        geb = dict(getattr(state, "gate_evidence_brief", {}) or {})
        if geb:
            for desc, brief in list(geb.items())[-6:]:
                lines.append(f"- Gate evidence: {brief}")
        return lines

    @staticmethod
    def _strategy_memory_lines(state: CyberGymState) -> List[str]:
        attempts = [
            item for item in list(state.attempt_history or [])
            if isinstance(item, dict)
        ][-12:]
        reflections = [
            item for item in list(getattr(state, "reflection_history", []) or [])
            if isinstance(item, dict)
        ][-4:]
        latest_reflection = ""
        if reflections:
            latest = reflections[-1]
            summary = str(latest.get("summary") or "")
            next_step = str(latest.get("next_step") or "")
            latest_reflection = f"{summary} Next: {next_step}".strip()
        elif state.reflection_note:
            latest_reflection = str(state.reflection_note or "")

        if not attempts and not latest_reflection:
            return []

        lines: List[str] = []
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in attempts:
            family = str(item.get("strategy_family") or "?").strip() or "?"
            record = grouped.setdefault(
                family,
                {
                    "count": 0,
                    "result": "",
                    "feedback": "",
                    "next": "",
                },
            )
            record["count"] += 1
            record["result"] = str(item.get("observed_result") or "?")
            record["feedback"] = str(item.get("stable_feedback") or "")
            record["next"] = str(item.get("next_hypothesis") or "")

        for family, record in list(grouped.items())[-4:]:
            # P23: increased truncation limits so diagnostic details that
            # distinguish one failure from another survive the render.
            result = record["result"]
            feedback = record["feedback"]
            next_hypothesis = record["next"]
            suffix = f"; feedback={feedback}" if feedback else ""
            if next_hypothesis:
                suffix += f"; next={next_hypothesis}"
            lines.append(f"- Tried `{family}` {record['count']}x: {result}{suffix}")

        if latest_reflection:
            lines.append(f"- Latest reflection: {latest_reflection}")
        lines.append(f"- Full ledger: `{(PROJECT_ARTIFACT_ROOT / 'strategy' / 'LEDGER.md').as_posix()}`")
        return lines

    @staticmethod
    def _task_bootstrap_line(state: CyberGymState) -> str:
        task = str(state.task or "").strip().replace("\n", " ")
        if not task:
            return ""
        return task

    @staticmethod
    def _is_default_task_objective(task: str) -> bool:
        normalized = " ".join(str(task or "").lower().split())
        return (
            "generate the exploit poc using the files in the current working directory" in normalized
            and "read readme.md first" in normalized
            and "single raw input file" in normalized
        )

    @staticmethod
    def _state_line(state: CyberGymState) -> str:
        return f"STATE {ValidationMixin._derive_control_mode(state)}"

    @staticmethod
    def _state_block_lines(state: CyberGymState) -> List[str]:
        lines = [f"- State: `{MemoryMixin._state_line(state).replace('STATE ', '')}`"]
        lines.append(f"- Phase: `{state.current_phase}`")
        crash_loc = getattr(state, "crash_location", "") or ""
        if crash_loc:
            lines.append(f"- Crash location: `{crash_loc}`")
        ready_paths = ValidationMixin._candidate_ready_submit_paths(state, include_active=True)
        if ready_paths:
            chunks = ValidationMixin._render_candidate_path_chunks(ready_paths)
            if chunks:
                lines.append(f"- Complete Ready PoC Submit List ({len(ready_paths)}): {chunks[0]}")
                for chunk in chunks[1:]:
                    lines.append(f"- Complete Ready PoC Submit List Continued: {chunk}")
                lines.append("- Required Action: submit every path in the complete list now.")
        if getattr(state, "phase_local_steps", 0):
            lines.append(f"- Phase Local Steps: `{state.phase_local_steps}`")
        if getattr(state, "mode_local_steps", 0):
            lines.append(f"- Mode Local Steps: `{state.mode_local_steps}`")
        if state.pending_reflection:
            lines.append("- Required Action: `record_reflection`")
        if state.discriminant_failed:
            lines.append("- **DISCRIMINANT FAILED**: Fixed binary also crashes — reduce overflow magnitude")
        elif state.best_poc_score == 1 and not state.is_verified():
            lines.append("- **PARTIAL HIT**: Vulnerable binary crashes but precision is unverified — refine for minimal overflow")
        submitted_fps = state.submitted_fingerprints or state.metadata.get("submitted_candidate_fingerprints", [])
        if submitted_fps:
            lines.append(f"- Submitted PoCs: {len(submitted_fps)} distinct")
        # Auto-deepen hints from graph analysis (3-step TTL, not one-shot)
        deepen_hints = list(getattr(state, "metadata", {}).get("_auto_deepen_hints", []) or [])
        hint_ages = dict(getattr(state, "metadata", {}).get("_auto_deepen_hint_ages", {}) or {})
        remaining = []
        for hint in deepen_hints:
            age = hint_ages.get(hint, 0)
            lines.append(f"- {hint}")
            if age < 3:
                remaining.append(hint)
                hint_ages[hint] = age + 1
        state.metadata["_auto_deepen_hints"] = remaining if remaining else None
        state.metadata["_auto_deepen_hint_ages"] = {
            h: a for h, a in hint_ages.items() if h in remaining
        }
        # Description symbol validation hints
        desc_hints = list(getattr(state, "metadata", {}).get("_desc_validation_hints", []) or [])
        for hint in desc_hints:
            lines.append(f"- {hint}")
        state.metadata.pop("_desc_validation_hints", None)  # show once
        # Callee-check gate hints
        callee_hints = list(getattr(state, "metadata", {}).get("_callee_gate_hints", []) or [])
        for hint in callee_hints:
            lines.append(f"- {hint}")
        state.metadata.pop("_callee_gate_hints", None)  # show once
        # Persistent description anchor staleness warning
        for c in (getattr(state, "sink_candidates", []) or []):
            if c.status != "eliminated" and (c.metadata or {}).get("description_anchor_stale"):
                lines.append(
                    f"- [WARNING] Sink candidate '{c.function}' derived from description but "
                    f"graph suggests it's NOT the actual sink (conf={c.confidence:.1f}). "
                    "Check deeper callees."
                )
        return lines

    @staticmethod
    def _recent_exploration_note_lines(state: CyberGymState) -> List[str]:
        lines: List[str] = []
        for item in state.exploration_notes[-4:]:
            if not isinstance(item, dict):
                continue
            note_type = str(item.get("note_type") or "").strip()
            if note_type == "hypothesis":
                lines.append(
                    "- NOTE hypothesis"
                    f" family={str(item.get('strategy_family') or '?')}"
                    f" target={str(item.get('target_surface') or '?')}"
                    f" reason={str(item.get('reason') or '')}"
                )
            elif note_type == "submission":
                lines.append(
                    "- NOTE submission"
                    f" family={str(item.get('strategy_family') or '?')}"
                    f" path={str(item.get('poc_path') or '?')}"
                    f" result={str(item.get('observed_result') or '?')}"
                    f" feedback={str(item.get('stable_feedback') or '')}"
                )
            elif note_type == "reflection":
                lines.append(
                    "- NOTE reflection "
                    + str(item.get("summary") or "")
                )
        return lines
