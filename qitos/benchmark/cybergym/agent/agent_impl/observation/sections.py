"""Observation section renderers — 5-section high-density model.

Sections:
1. Vulnerability — bug/task identity (merges old Mission + Assessment highlights)
2. Sink Candidates — sinks + active sink call chain (merges old Assessment sinks + Vulnerability Path)
3. Constraint Board — gate table + BLOCKED line (merges old Required Conditions + Next Action)
4. Experiments — PoC attempts with key_insight (simplified old Experiments)
5. Task Memory — persistent facts and notes (new, from durable_feedback_facts + exploration_notes)
"""
from __future__ import annotations

import re as _re
from typing import Any, Dict, List, Optional

from ...state import CyberGymState


class SectionMixin:
    """Mixin providing the five observation section renderers."""

    # ====================================================================
    # Section 1: Vulnerability
    # ====================================================================

    @staticmethod
    def _render_vulnerability(state: CyberGymState) -> str:
        """Concise bug/task identity — what we know about the vulnerability."""
        lines = ["## Vulnerability"]

        # --- Core identity ---
        vuln_desc = str(getattr(state, "vulnerability_description", "") or "").strip()
        phase = str(getattr(state, "current_phase", "") or "")
        max_len = 500 if phase == "ingestion" else 300
        if len(vuln_desc) > max_len:
            tech_terms = {
                'overflow', 'buffer', 'free', 'uninitialized', 'out-of-bounds',
                'memcpy', 'size', 'length', 'offset', 'heap', 'stack',
                'null', 'deref', 'cve', 'crash', 'trigger', 'integer',
                'signed', 'unsigned', 'underflow', 'use-after', 'double-free',
            }
            sentences = [s.strip() for s in _re.split(r'[.!?]', vuln_desc) if len(s.strip()) > 20]
            if sentences:
                scored = [(sum(1 for t in tech_terms if t in s.lower()), -i, s) for i, s in enumerate(sentences)]
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                vuln_desc = scored[0][2][:max_len - 3] + "..."
            else:
                vuln_desc = vuln_desc[:max_len - 3] + "..."

        metadata = getattr(state, "metadata", {}) or {}
        bug_type = str(getattr(state, "bug_type", "") or "").strip()
        confirmed_crash = str(getattr(state, "crash_type", "") or "").strip()
        crash_prior = str(metadata.get("crash_type_prior", "") or "").strip()
        crash_type = confirmed_crash or crash_prior or "UNSET"
        strategy = str(getattr(state, "poc_strategy", "") or "").strip()
        input_fmt = getattr(state, "input_format", None)
        if input_fmt and hasattr(input_fmt, "format_type") and input_fmt.format_type:
            input_fmt = str(input_fmt.format_type)
        elif input_fmt and hasattr(input_fmt, "mutation_strategy") and input_fmt.mutation_strategy:
            input_fmt = str(input_fmt.mutation_strategy)
        else:
            input_fmt = ""

        lines.append(vuln_desc)
        lines.append("")

        # Info tags
        tags = []
        if bug_type:
            tags.append(f"bug={bug_type}")
        if crash_type and crash_type != "UNSET":
            tags.append(f"crash={crash_type}")
        if strategy:
            tags.append(f"strategy={strategy}")
        if input_fmt:
            tags.append(f"fmt={input_fmt}")
        if tags:
            lines.append(" | ".join(tags))

        # Harness info
        resolution = getattr(state, "harness_resolution", None)
        if resolution and getattr(resolution, "status", "") == "resolved":
            selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
            candidates = list(getattr(state, "harness_candidates", []) or [])
            selected = next((c for c in candidates if c.candidate_id == selected_id), None)
            if selected:
                entry_fn = selected.entry_function or ""
                src = selected.source_path or ""
                lines.append(f"harness: `{entry_fn}` ({src})")

        return "\n".join(lines)

    # ====================================================================
    # Section 2: Sink Candidates
    # ====================================================================

    @staticmethod
    def _render_sink_candidates(state: CyberGymState) -> str:
        """Sink list with active sink call chain."""
        lines = ["## Sink Candidates"]

        all_sinks = list(getattr(state, "sink_candidates", []) or [])
        active_sinks = [s for s in all_sinks if s.status not in ("eliminated", "provisional")]
        confirmed_sinks = list(state.confirmed_sink_candidates())
        active_sink_id = str(getattr(state, "active_sink_candidate_id", "") or "")

        if not active_sinks:
            lines.append("- No sink candidates identified yet.")
            lines.append("")
            lines.append("Use `grep`/`read` to find the vulnerable function, then `sink(action=\"add\", ...)` to record it.")
            return "\n".join(lines)

        # Number sinks
        sink_ids = {}
        for i, s in enumerate(active_sinks[:8], 1):
            sink_ids[s.candidate_id] = f"s{i}"

        for s in active_sinks[:8]:
            sid = sink_ids.get(s.candidate_id, "?")
            is_active = s.candidate_id == active_sink_id
            marker = "►" if is_active else " "
            role = s.metadata.get("candidate_role", "") if isinstance(s.metadata, dict) else ""
            role_tag = f" [{role}]" if role and role != "unknown" else ""
            conf = f"{s.confidence:.2f}" if s.confidence else "?"
            status_tag = " ✓" if s.status == "confirmed" else ""
            loc = s.location or ""
            lines.append(f"- {marker} [{sid}] `{s.function}` @ {loc} (conf={conf}{role_tag}{status_tag})")
            if s.evidence:
                lines.append(f"      {s.evidence[:120]}")

        # Call chain for active sink
        if confirmed_sinks:
            active_sink = next(
                (s for s in confirmed_sinks if s.candidate_id == active_sink_id),
                confirmed_sinks[0]
            )
            nodes = list(getattr(state, "call_chain_nodes", []) or [])
            gates = list(getattr(state, "call_chain_gates", []) or [])

            # Filter nodes/gates to this sink
            target_sink = active_sink.candidate_id
            sink_nodes = [n for n in nodes if not n.sink_id or n.sink_id == target_sink]
            sink_gates = [g for g in gates if not g.sink_id or g.sink_id == target_sink]

            if sink_nodes:
                lines.append("")
                lines.append(f"Call chain for [{sink_ids.get(target_sink, '?')}]:")
                sorted_nodes = sorted(sink_nodes, key=lambda n: n.order)
                gates_by_order = {}
                for g in sink_gates:
                    gates_by_order.setdefault(g.node_order, []).append(g)

                chain_parts = []
                for node in sorted_nodes:
                    role_tag = f"({node.role})" if node.role else ""
                    node_gates = gates_by_order.get(node.order, [])
                    if node_gates:
                        confirmed = sum(1 for g in node_gates if g.status == "confirmed")
                        total = len(node_gates)
                        gate_str = f"[{confirmed}/{total}]"
                    else:
                        gate_str = "[--]"
                    chain_parts.append(f"{node.function}{role_tag}{gate_str}")

                lines.append("  " + " → ".join(chain_parts))

                # Gate details for active sink
                if sink_gates:
                    lines.append("")
                    for g in sink_gates[:8]:
                        status_icon = {"confirmed": "✓", "refuted": "✗"}.get(g.status, "?")
                        cond = str(g.required_condition or g.description or "")[:80]
                        lines.append(f"  {status_icon} {g.gate_type}: {cond}")

        elif not confirmed_sinks and active_sinks:
            # Show localization guidance
            lines.append("")
            lines.append("**Guidance**: Confirm a sink by reading the vulnerable function, then:")
            lines.append('  `sink(action="add", function="func", evidence="code evidence", candidate_role="crash_site")`')

        return "\n".join(lines)

    # ====================================================================
    # Section 3: Constraint Board
    # ====================================================================

    @staticmethod
    def _render_constraint_board(state: CyberGymState) -> str:
        """Gate table filtered to active sink + BLOCKED line."""
        lines = ["## Constraint Board"]

        # --- BLOCKED line from Next Action contract ---
        from ..core.runtime_context_contract import derive_contract_next_action_block
        next_block = derive_contract_next_action_block(state)
        if next_block:
            lines.append(f"**BLOCKED**: {next_block.get('required', '')}")
            why = next_block.get("why", "")
            if why:
                lines.append(f"- Why: {why}")
            target = next_block.get("target", "")
            if target:
                lines.append(f"- Target: {target}")
            do_not = next_block.get("do_not", "")
            if do_not:
                lines.append(f"- Do not: {do_not}")
            lines.append("")

        # --- Ready PoC submit prompt ---
        ready_paths = []
        for item in list(getattr(state, "ready_pocs", []) or []):
            if getattr(item, "file_path", "") and getattr(item, "ready_to_submit", True):
                ready_paths.append(item.file_path)
        if ready_paths:
            lines.append(f"**SUBMIT**: `submit_poc(\"{ready_paths[0]}\", key_insight=\"...\")`")
            if len(ready_paths) > 1:
                lines.append(f"- {len(ready_paths)} PoCs ready to submit")
            lines.append("")

        # --- Gate table ---
        gates = list(getattr(state, "call_chain_gates", []) or [])
        active_sink_id = str(getattr(state, "active_sink_candidate_id", "") or "")
        # record_gate uses _primary_sink_id() which returns "function@location" format,
        # while active_sink_candidate_id uses "sink_HASH" format. Accept both.
        primary_sink = str(getattr(state, "_primary_sink_id", lambda: "")()) if hasattr(state, "_primary_sink_id") else ""

        # Filter to active sink (match either identifier format)
        if active_sink_id or primary_sink:
            filtered = [g for g in gates if not g.sink_id or g.sink_id == active_sink_id or g.sink_id == primary_sink]
        else:
            filtered = gates

        if not filtered:
            lines.append("No constraints recorded yet. Use `record_gate(...)` to capture path conditions.")
            return "\n".join(lines)

        # Sort: confirmed first, then open, then refuted
        status_order = {"confirmed": 0, "inferred": 1, "unknown": 1, "questioned": 1, "bypassed": 2, "refuted": 3}
        sorted_gates = sorted(filtered, key=lambda g: (status_order.get(g.status, 2), g.node_order))

        lines.append("| Gate | Type | Condition | Status | Source |")
        lines.append("|------|------|-----------|--------|--------|")

        for g in sorted_gates[:15]:
            status_icon = {"confirmed": "✓", "refuted": "✗"}.get(g.status, "?")
            gate_type = g.gate_type.replace("_gate", "")
            cond = str(g.required_condition or g.description or "")[:60]
            source = str(g.evidence or "")[:30]
            lines.append(f"| {status_icon} | {gate_type} | {cond} | {g.status} | {source} |")

        # Summary
        confirmed_count = sum(1 for g in filtered if g.status == "confirmed")
        open_count = sum(1 for g in filtered if g.status in ("inferred", "unknown", "questioned"))
        refuted_count = sum(1 for g in filtered if g.status == "refuted")
        lines.append("")
        lines.append(f"✓ {confirmed_count} confirmed · ? {open_count} open · ✗ {refuted_count} refuted")

        # Primary blocker
        first_open = None
        for g in sorted_gates:
            if g.status in ("inferred", "unknown", "questioned"):
                first_open = g
                break
        if first_open:
            cond = str(first_open.required_condition or first_open.description or "").strip()
            lines.append(f"**Blocker**: {cond}")

        # Recipe / mutation targets
        _meta = getattr(state, "metadata", {}) or {}
        recipe = state.get_poc_recipe() if hasattr(state, "get_poc_recipe") else (_meta.get("poc_recipe") or {})
        trigger_mutations = recipe.get("trigger_mutations", [])
        if trigger_mutations:
            lines.append("")
            lines.append("**Mutation targets**:")
            for mut in trigger_mutations[:4]:
                desc = str(mut.get("description") or mut.get("target_field") or "")[:80]
                lines.append(f"- {desc}")

        return "\n".join(lines)

    # ====================================================================
    # Section 4: Experiments
    # ====================================================================

    @staticmethod
    def _render_experiments(state: CyberGymState) -> str:
        """PoC attempt history with key_insight and runtime evidence."""
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")
        poc_attempts = int(getattr(state, "poc_attempts", 0) or 0)

        if poc_attempts == 0 or phase in ("ingestion", "exploration"):
            return "## Experiments\n- No PoC submissions yet."

        lines = ["## Experiments"]

        consecutive = int(getattr(state, "consecutive_misses", 0) or 0)
        if consecutive > 0:
            lines.append(f"({poc_attempts} attempts, {consecutive} consecutive no-crash)")
        else:
            lines.append(f"({poc_attempts} attempts)")

        # --- PoC table with key_insight ---
        trimmed = state.hot_feedback_window[-5:]
        if trimmed:
            lines.append("")
            lines.append("| # | PoC | Result | Key Insight |")
            lines.append("|---|-----|--------|------------|")
            for i, item in enumerate(trimmed, 1):
                poc_path = str(getattr(item, "poc_path", "") or "")
                poc_name = poc_path.split("/")[-1] if poc_path else "?"
                exit_code = getattr(item, "exit_code", 0)
                if exit_code != 0 and exit_code is not None:
                    result = "CRASH"
                else:
                    assessment = str(getattr(item, "assessment", "") or "").strip()
                    if "triggered" in assessment.lower():
                        result = "TRIGGERED"
                    else:
                        result = "NO_CRASH"
                # key_insight from FeedbackRecord, fallback to suggested_action
                key_insight = str(getattr(item, "key_insight", "") or "").strip()
                if not key_insight:
                    key_insight = str(getattr(item, "suggested_action", "") or "").strip()
                if not key_insight:
                    key_insight = "-"
                lines.append(f"| {i} | {poc_name} | {result} | {key_insight[:80]} |")

        # --- Runtime evidence for latest attempt ---
        runtime_records = [
            item for item in list((state.metadata or {}).get("runtime_evidence", []) or [])
            if isinstance(item, dict)
        ]
        if runtime_records:
            latest = runtime_records[-1]
            source = str(latest.get("source_kind") or latest.get("source") or "")
            if source == "gdb_debug":
                output = str(latest.get("output") or "").strip()
                if output:
                    lines.append("")
                    lines.append("**Latest gdb_debug**:")
                    for oline in output.split("\n")[:8]:
                        if oline.strip():
                            lines.append(f"  {oline.strip()[:120]}")

        # --- Negative evidence ---
        negative_evidence = list(
            (state.metadata or {}).get("negative_evidence", [])
            if isinstance(state.metadata, dict) else []
        )
        active_ne = [ev for ev in negative_evidence if ev.get("ttl", 0) > 0]
        if active_ne:
            lines.append("")
            lines.append(f"**Negative evidence** ({len(active_ne)} active):")
            for ev in active_ne[-3:]:
                kind = ev.get("kind", "unknown")
                summary = str(ev.get("summary", ""))[:100]
                lines.append(f"- [{kind}] {summary}")

        # --- Pattern analysis ---
        if consecutive >= 3:
            lines.append("")
            lines.append(
                f"**Pattern**: {consecutive} consecutive no-crash. "
                "Use gdb_debug to diagnose: path not reached vs. trigger condition unmet."
            )

        # --- Crash stack ---
        crash_stack = str(getattr(state, "crash_stack", "") or "").strip()
        if crash_stack:
            lines.append("")
            lines.append(f"**Crash stack**: {crash_stack[:200]}")

        return "\n".join(lines)

    # ====================================================================
    # Section 5: Task Memory
    # ====================================================================

    @staticmethod
    def _render_task_memory(state: CyberGymState) -> str:
        """Persistent facts and notes across iterations."""
        lines = ["## Task Memory"]
        has_content = False

        # --- Durable feedback facts ---
        durable = list(getattr(state, "durable_feedback_facts", []) or [])
        if durable:
            has_content = True
            for fact in durable[:6]:
                lines.append(f"- {str(fact)[:120]}")

        # --- Exploration notes (model-writable via existing mechanism) ---
        notes = list(getattr(state, "exploration_notes", []) or [])
        if notes:
            has_content = True
            # Show most recent notes first
            for note in reversed(notes[-8:]):
                if isinstance(note, dict):
                    nt = str(note.get("note_type") or "").strip()
                    content = str(note.get("content") or note.get("text") or "").strip()
                    if content:
                        tag = f"[{nt}] " if nt else ""
                        lines.append(f"- {tag}{content[:120]}")
                elif isinstance(note, str) and note.strip():
                    lines.append(f"- {note.strip()[:120]}")

        # --- Refuted approaches ---
        refuted_gates = state.refuted_gates() if hasattr(state, "refuted_gates") else []
        if refuted_gates:
            has_content = True
            lines.append("")
            lines.append("**Avoid**:")
            for g in refuted_gates[:4]:
                cond = str(g.description or "")[:80]
                repair = str(g.repair_hint or "")[:60]
                entry = f"- ✗ {cond}"
                if repair:
                    entry += f" → try: {repair}"
                lines.append(entry)

        if not has_content:
            lines.append("- No persistent notes yet.")

        return "\n".join(lines)

    # ====================================================================
    # Phase tools (preserved for compatibility)
    # ====================================================================

    @staticmethod
    def _render_phase_tools(state: CyberGymState) -> List[str]:
        """Return phase-appropriate tool usage hints."""
        return []
