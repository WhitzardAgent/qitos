"""Observation section renderers — extracted from renderer.py."""
from __future__ import annotations

import re as _re
from typing import Any, Dict, List, Optional

from ...analysis.ir_renderer import IRRenderer
from ...state import CyberGymState


class SectionMixin:
    """Mixin providing the six observation section renderers and phase tools."""

    @staticmethod
    def _render_mission(state: CyberGymState) -> str:
        """Section 1: Mission — minimal task identity, no repetition."""
        vuln_desc = str(getattr(state, "vulnerability_description", "") or "").strip()
        # Preserve key technical details — 120 chars was too short, cutting off
        # buffer sizes, function parameters, and trigger conditions.
        phase = str(getattr(state, "current_phase", "") or "")
        if phase == "ingestion":
            # Full description during ingestion — the LLM needs it for analysis
            max_len = 500
        else:
            max_len = 300
        if len(vuln_desc) > max_len:
            # Try sentence-level scoring to keep the most informative part
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
                best_sentence = scored[0][2]
                if len(best_sentence) > max_len:
                    vuln_desc = best_sentence[:max_len - 3] + "..."
                else:
                    vuln_desc = best_sentence
            else:
                vuln_desc = vuln_desc[:max_len - 3] + "..."
        metadata = getattr(state, "metadata", {}) or {}
        bug_type = str(getattr(state, "bug_type", "") or "").strip()
        confirmed_crash = str(getattr(state, "crash_type", "") or "").strip()
        crash_prior = str(metadata.get("crash_type_prior", "") or "").strip()
        crash_source = str(
            metadata.get("crash_type_source")
            or metadata.get("crash_type_prior_source")
            or ""
        ).strip()
        crash_type = confirmed_crash or crash_prior or "UNSET"
        strategy = str(getattr(state, "poc_strategy", "") or "").strip()
        input_fmt = getattr(state, "input_format", None)
        if input_fmt and hasattr(input_fmt, "format_type") and input_fmt.format_type:
            input_fmt = str(input_fmt.format_type)
        elif input_fmt and hasattr(input_fmt, "mutation_strategy") and input_fmt.mutation_strategy:
            input_fmt = str(input_fmt.mutation_strategy)
        else:
            input_fmt = ""
        likely_targets = list(getattr(state, "likely_fuzz_targets", []) or [])

        lines = ["## Mission"]
        lines.append(f"- Vulnerability: {vuln_desc}")
        type_parts = []
        if bug_type:
            type_parts.append(f"Bug type: {bug_type}")
        if crash_type != "UNSET":
            source_suffix = f" [source: {crash_source}]" if crash_source else ""
            if confirmed_crash and crash_source == "submit_poc":
                type_parts.append(f"Crash type: {crash_type}{source_suffix}")
            else:
                type_parts.append(f"Crash type prior: {crash_type}{source_suffix}")
        else:
            type_parts.append("Crash type: UNSET")
        lines.append("- " + " | ".join(type_parts))
        strat_parts = []
        if strategy:
            strat_parts.append(f"Strategy: {strategy}")
        if input_fmt and input_fmt != "unknown":
            strat_parts.append(f"Input: {input_fmt}")
        consumption_summary = IRRenderer.render_harness_consumption(
            getattr(getattr(state, "input_format", None), "consumption", None),
            mode="summary",
        )
        if consumption_summary:
            strat_parts.append(consumption_summary)
        if strat_parts:
            lines.append("- " + " | ".join(strat_parts))
        if likely_targets:
            lines.append(f"- Likely harness: {likely_targets[0]} [source: description]")
        lines.append("- Success: submit_poc returns triggered")
        return "\n".join(lines)

    @staticmethod
    def _render_current_assessment(state: CyberGymState) -> str:
        """Section 2: Current Assessment — confirmed/likely/unknown/rejected."""
        current_step = int(getattr(state, "current_step", 0) or 0)
        lines = ["## Current Assessment"]
        metadata = getattr(state, "metadata", {}) or {}

        # --- Fix A: Hard contract slots (objective + consistency first) ---
        from ..core.runtime_context_contract import render_context_contract_slots
        contract = render_context_contract_slots(state)
        assessment_slots = contract.get("assessment", [])
        if assessment_slots:
            lines.append("")
            lines.append("### Runtime Contract")
            lines.extend(assessment_slots)

        # --- Confirmed ---
        confirmed_items: List[str] = []
        # Sinks (mark which one is active)
        active_id = str(getattr(state, "active_sink_candidate_id", "") or "")
        for c in state.confirmed_sink_candidates():
            source = str(getattr(state, "sink_hypothesis_source", "") or c.source or "code reading")
            active_tag = " ◀ ACTIVE" if c.candidate_id == active_id else ""
            meta = dict(getattr(c, "metadata", {}) or {})
            role = str(meta.get("candidate_role") or "unknown")
            path_id = str(meta.get("ranked_path_id") or "")
            role_text = f" role={role}" if role else ""
            path_text = f" path={path_id}" if path_id else ""
            if meta.get("needs_downstream_endpoint"):
                role_text += " needs_downstream_endpoint"
            confirmed_items.append(
                f"- Sink: `{c.function}` @{c.location} "
                f"{role_text}{path_text} [source: {source}, conf={c.confidence:.1f}]{active_tag}"
            )
            # Show evidence brief for this sink if available
            evidence_brief = dict(getattr(state, "gate_evidence_brief", {}) or {})
            if evidence_brief and c.candidate_id in evidence_brief:
                brief_text = str(evidence_brief[c.candidate_id] or "").strip()
                if brief_text:
                    confirmed_items.append(f"  evidence: {brief_text[:120]}")
        # Harness
        if getattr(state, "harness_entry_confirmed", False):
            harness_candidates = list(getattr(state, "harness_candidates", []) or [])
            if harness_candidates:
                selected_id = str(getattr(getattr(state, "harness_resolution", None), "selected_candidate_id", "") or "")
                h = next((item for item in harness_candidates if item.candidate_id == selected_id), harness_candidates[0])
                confirmed_items.append(
                    f"- Harness: `{h.entry_function or 'entry'}` @{h.source_path}:{h.line} "
                    f"[source: code reading]"
                )
        consumption = getattr(getattr(state, "input_format", None), "consumption", None)
        if consumption and getattr(consumption, "status", "") == "success":
            rendered_consumption = IRRenderer.render_harness_consumption(consumption, mode="evidence", max_evidence=3)
            if rendered_consumption:
                confirmed_items.extend(rendered_consumption.splitlines())
        # Crash type: only submit_poc sanitizer feedback is confirmed.
        crash_type = str(getattr(state, "crash_type", "") or "").strip()
        crash_source = str(metadata.get("crash_type_source") or "").strip()
        if crash_type and crash_type != "UNSET" and crash_source == "submit_poc":
            confirmed_items.append(f"- Crash type: {crash_type} [source: submit_poc]")
        # Bug mechanism from trigger_hypothesis
        trigger = str(getattr(state, "trigger_hypothesis", "") or "").strip()
        if trigger:
            confirmed_items.append(f"- Bug mechanism: {trigger} [source: code reading]")
        # Submit feedback confirmation
        poc_attempts = int(getattr(state, "poc_attempts", 0) or 0)
        if poc_attempts > 0:
            last_result = getattr(state, "last_verification_result", None)
            if last_result:
                vul_exit = last_result.get("vul_exit_code")
                if vul_exit is not None:
                    confirmed_items.append(
                        f"- PoC reaches harness (vul_exit={vul_exit}) [source: submit_poc]"
                    )

        if confirmed_items:
            lines.append("")
            lines.append("### Confirmed")
            lines.extend(confirmed_items)
        else:
            lines.append("")
            lines.append("### Confirmed")
            lines.append("- (nothing yet)")

        # --- Likely ---
        likely_items: List[str] = []
        vuln_files = list(getattr(state, "vulnerable_files", []) or [])
        vuln_funcs = list(getattr(state, "vulnerable_functions", []) or [])
        crash_prior = str(metadata.get("crash_type_prior", "") or "").strip()
        prior_source = str(metadata.get("crash_type_prior_source", "") or "").strip()
        if crash_prior and not (crash_type and crash_source == "submit_poc"):
            likely_items.append(
                f"- Crash type prior: {crash_prior} "
                f"[source: {prior_source or 'description prior'}]"
            )
        analysis = getattr(state, "description_analysis", None)
        if analysis and str(getattr(analysis, "status", "") or "") not in ("", "pending"):
            tags = list(getattr(analysis, "mechanism_tags", []) or [])
            ops = list(getattr(analysis, "described_operations", []) or [])
            if tags or ops:
                brief = ", ".join([*tags[:4], *ops[:3]])
                likely_items.append(
                    f"- Description mechanisms: {brief} "
                    "[source: description prior]"
                )
        verified_refs = list(getattr(state, "verified_search_refs", []) or [])
        for ref in verified_refs[:6]:
            likely_items.append(IRRenderer.render_verified_ref(ref))
        if vuln_files:
            likely_items.append(
                f"- Vulnerable files: {', '.join(vuln_files[:5])} [source: investigation]"
            )
        if vuln_funcs:
            likely_items.append(
                f"- Vulnerable functions: {', '.join(vuln_funcs[:5])} [source: investigation]"
            )
        # Navigation candidates
        for c in state.navigation_candidates():
            meta = dict(getattr(c, "metadata", {}) or {})
            role = str(meta.get("candidate_role") or meta.get("role") or "unknown")
            path_id = str(meta.get("ranked_path_id") or "")
            selection = str(meta.get("selection_status") or "unreviewed")
            lead_label = "Lead" if role == "path_anchor" or meta.get("needs_downstream_endpoint") else "Possible sink"
            path_text = f" path={path_id}" if path_id else ""
            likely_items.append(
                f"- {lead_label}: `{c.function}` @{c.location} role={role}{path_text} "
                f"selection={selection} [source: {c.source}, conf={c.confidence:.1f}]"
            )
        # Input format hint
        input_model = getattr(state, "input_format", None)
        if input_model and hasattr(input_model, "format_type") and input_model.format_type:
            likely_items.append(
                f"- Input format: {input_model.format_type} [source: analysis]"
            )
        consumption = getattr(input_model, "consumption", None) if input_model else None
        if consumption and getattr(consumption, "status", "") == "partial":
            rendered_consumption = IRRenderer.render_harness_consumption(consumption, mode="evidence", max_evidence=3)
            if rendered_consumption:
                likely_items.extend(rendered_consumption.splitlines())
        # Harness candidate (not yet confirmed)
        if not getattr(state, "harness_entry_confirmed", False):
            harness_candidates = list(getattr(state, "harness_candidates", []) or [])
            if harness_candidates:
                h = harness_candidates[0]
                likely_items.append(
                    f"- Harness: `{h.entry_function or 'entry'}` @{h.source_path}:{h.line} "
                    f"[source: bootstrap scan, not confirmed]"
                )

        if likely_items:
            lines.append("")
            lines.append("### Likely")
            lines.extend(likely_items)

        # --- Supplementary: feedback facts and suggested constraints ---
        supp_items: List[str] = []
        # Durable feedback facts (crash_type, crash_location from ASAN)
        for fact in list(getattr(state, "durable_feedback_facts", []) or [])[-6:]:
            fact_str = str(fact or "").strip()
            if fact_str and len(fact_str) > 5:
                supp_items.append(f"- {fact_str[:120]} [source: submit feedback]")
        # Durable code facts (function signatures, buffer sizes — non-numeric)
        for fact in list(getattr(state, "durable_code_facts", []) or [])[-6:]:
            fact_str = str(fact or "").strip()
            if fact_str and len(fact_str) > 5:
                supp_items.append(f"- {fact_str[:120]} [source: code reading]")
        # Suggested constraints (from analysis, pending LLM confirmation)
        suggested = list(getattr(state, "suggested_constraints", []) or [])
        for s in suggested[:3]:
            expr = str(s.get("expression") or s.get("description") or "").strip()
            role = str(s.get("role") or "unknown").strip()
            if expr:
                supp_items.append(f"- Suggested: [{role}] {expr[:100]} [source: analysis, not yet confirmed]")
        if supp_items:
            lines.append("")
            lines.append("### Supplementary")
            lines.extend(supp_items)

        # Assessment snippets are now in hard contract slots (Fix A)

        # --- Unknown ---
        unknown_items: List[str] = []
        if not crash_type and not crash_prior:
            unknown_items.append("- Crash type: not yet classified [source: unset]")
        desc_status = str(getattr(getattr(state, "description_analysis", None), "status", "") or "pending")
        if desc_status == "pending":
            unknown_items.append("- Description analysis: not yet structured [source: unset]")
        for hint in list(getattr(state, "unresolved_search_hints", []) or [])[:4]:
            unknown_items.append(IRRenderer.render_unresolved_hint(hint))
        if not getattr(state, "harness_entry_confirmed", False):
            unknown_items.append("- Harness: which fuzzer targets the vulnerability? [source: unresolved]")
        consumption = getattr(getattr(state, "input_format", None), "consumption", None)
        if consumption and getattr(consumption, "status", "") in {"partial", "unresolved"}:
            unknown_items.append(
                "- Harness consumption: partial/unknown; unresolved first hops remain possible path anchors [source: harness AST]"
            )
        # Check for open gates
        open_gates = state.open_gates()
        if open_gates:
            unknown_items.append(
                f"- {len(open_gates)} open constraint(s) — first: {open_gates[0].description[:80]} [source: analysis]"
            )
        # Analysis gaps from interprocedural analysis
        _meta = getattr(state, "metadata", {}) or {}
        _brief_sections = _meta.get("_analysis_brief_sections", {}) or {}
        gaps_text = str(_brief_sections.get("gaps", "") or "").strip()
        if gaps_text and not open_gates:
            # Show first gap as an unknown item
            first_gap = gaps_text.split("\n")[0][:100]
            unknown_items.append(f"- Analysis gap: {first_gap} [source: analysis service]")

        if unknown_items:
            lines.append("")
            lines.append("### Unknown")
            lines.extend(unknown_items)

        # --- Rejected ---
        rejected_items: List[str] = []
        stale_names = []
        for c in (getattr(state, "sink_candidates", []) or []):
            if c.status != "eliminated" and (c.metadata or {}).get("description_anchor_stale"):
                stale_names.append(c.function)
        if stale_names:
            rejected_items.append(
                f"- Description-derived candidates ({', '.join(stale_names[:5])}) — "
                f"not real function names, extracted by regex [source: description_anchor_stale]"
            )
        # Recent negative evidence as rejected/avoid items
        negative_evidence = list(
            metadata.get("negative_evidence", [])
            if isinstance(metadata, dict) else []
        )
        active_ne = [ev for ev in negative_evidence if ev.get("ttl", 0) > 0]
        for ev in active_ne[-3:]:
            kind = ev.get("kind", "unknown")
            summary = str(ev.get("summary", ""))[:100]
            avoid = str(ev.get("avoid_next", ""))
            if avoid:
                rejected_items.append(f"- [{kind}] {summary} — avoid: {avoid[:80]}")
            else:
                rejected_items.append(f"- [{kind}] {summary}")
        if rejected_items:
            lines.append("")
            lines.append("### Rejected")
            lines.extend(rejected_items)

        return "\n".join(lines)

    @staticmethod
    def _render_vulnerability_path(state: CyberGymState) -> str:
        """Section 3: Vulnerability Path — call chain as causal path diagram."""
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        gates = list(getattr(state, "call_chain_gates", []) or [])
        _meta = getattr(state, "metadata", {}) or {}
        _brief_sections = _meta.get("_analysis_brief_sections", {}) or {}
        _code_ctx = str(_meta.get("_code_context_markdown", "") or "").strip()

        # Determine visibility based on phase
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")
        if phase == "ingestion":
            return (
                "## Vulnerability Path\n"
                "- Pending: structure description, verify refs, then read the first code anchor. "
                "[source: unset]"
            )

        lines = ["## Vulnerability Path"]

        # --- Fix A: Hard contract slot for mechanism graph ---
        from ..core.runtime_context_contract import render_context_contract_slots
        contract = render_context_contract_slots(state)
        vuln_path_slots = contract.get("vuln_path", [])
        if vuln_path_slots:
            lines.extend(vuln_path_slots)
            lines.append("")

        ranked_paths = list(getattr(state, "ranked_vulnerability_paths", []) or [])
        if ranked_paths and not nodes:
            active_path_id = str(getattr(state, "selected_analysis_path_id", "") or "")
            phase_limit = 5 if phase in {"exploration", "ingestion"} else 3
            for idx, path in enumerate(ranked_paths[:phase_limit], 1):
                lines.append(IRRenderer.render_ranked_path(
                    path,
                    idx,
                    active=bool(active_path_id and path.get("path_id") == active_path_id),
                ))
            # Mechanism graph snippets now in hard contract slots (Fix A)
            return "\n".join(lines)

        if not nodes:
            # Try to show analysis service paths
            paths_text = str(_brief_sections.get("paths", "") or "").strip()
            if paths_text:
                lines.append("```\n(analysis service path — not yet confirmed by code reading)\n```")
                lines.append("")
                for path_line in paths_text.split("\n"):
                    if path_line.strip():
                        lines.append(path_line)
            else:
                lines.append("```\n??? (no chain nodes recorded yet)\n```")
            # Mechanism graph snippets now in hard contract slots (Fix A)
            # Still show code context callees if available
            if _code_ctx:
                callee_lines = [l for l in _code_ctx.split("\n") if "Callees:" in l or "Focus:" in l]
                if callee_lines:
                    lines.append("")
                    lines.append("**Code context**:")
                    lines.extend(callee_lines[:4])
            return "\n".join(lines)

        # Build path diagram
        # Group gates by node_order
        gates_by_order: Dict[int, List] = {}
        for g in gates:
            gates_by_order.setdefault(g.node_order, []).append(g)

        # Render each node as a path element
        path_parts = []
        for i, node in enumerate(sorted(nodes, key=lambda n: n.order)):
            # Node label
            role_tag = f" ({node.role})" if node.role else ""
            node_label = f"{node.function}{role_tag}"

            # Location
            loc = f"@{node.location}" if node.location else ""

            # Gate status
            node_gates = gates_by_order.get(node.order, [])
            if node_gates:
                gate_strs = []
                confirmed_count = 0
                total_count = len(node_gates)
                for g in node_gates:
                    if g.status == "confirmed":
                        gate_strs.append("✓")
                        confirmed_count += 1
                    elif g.status == "refuted":
                        gate_strs.append("✗")
                    elif g.status == "questioned":
                        gate_strs.append("?")
                    else:
                        gate_strs.append("?")
                gate_summary = f"{confirmed_count}/{total_count}"
                gate_type_strs = [g.gate_type for g in node_gates]
                gate_info = f"{''.join(gate_strs)} {','.join(set(gate_type_strs))} ({gate_summary})"
            else:
                gate_info = "— no gates"

            path_parts.append((node_label, loc, gate_info))

        # Format as diagram
        diagram_lines = ["```"]
        for i, (label, loc, gate_info) in enumerate(path_parts):
            if i == 0:
                diagram_lines.append(f"{label}")
                if loc:
                    diagram_lines.append(f"     {loc}")
                diagram_lines.append(f"     {gate_info}")
            else:
                diagram_lines.append(f"  ──→ {label}")
                if loc:
                    diagram_lines.append(f"       {loc}")
                diagram_lines.append(f"       {gate_info}")
        diagram_lines.append("```")

        # Numerical constraints from derive_numerical_constraints()
        numerical = state.derive_numerical_constraints()
        if numerical:
            diagram_lines.append("")
            diagram_lines.append("Numerical: " + "; ".join(numerical[:4]))

        # Gate legend
        diagram_lines.append("")
        diagram_lines.append("Gate legend: ✓ confirmed, ? inferred, ✗ refuted, — no gates")

        lines.extend(diagram_lines)

        # Code context: callees and risk signals from READ analysis
        if _code_ctx:
            ctx_lines = [l for l in _code_ctx.split("\n")
                         if "Callees:" in l or "Focus:" in l or "RISKY" in l]
            if ctx_lines:
                lines.append("")
                lines.append("**Code context**:")
                lines.extend(ctx_lines[:4])

        # Mechanism graph snippets now in hard contract slots (Fix A)

        return "\n".join(lines)

    @staticmethod
    def _render_required_conditions(state: CyberGymState) -> str:
        """Section 4: Required Conditions — PoC recipe + constraints.

        Rendering order (recipe-first):
        1. Concrete mutation targets (from poc_recipe.trigger_mutations)
        2. Carrier / seed strategy
        3. PoC sanity / carrier checks
        4. Input mappings
        5. Confirmed / open gates
        6. Trigger conditions from analysis
        7. Open mapping gaps
        8. Refuted conditions
        """
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")

        # Phase visibility: hidden during ingestion
        if phase == "ingestion":
            return (
                "## Required Conditions\n"
                "- Pending: no source-backed path or gate has been confirmed yet. "
                "[source: unset]"
            )

        gates = list(getattr(state, "call_chain_gates", []) or [])
        _meta = getattr(state, "metadata", {}) or {}
        _brief_sections = _meta.get("_analysis_brief_sections", {}) or {}
        _code_ctx = str(_meta.get("_code_context_markdown", "") or "").strip()
        recipe = state.get_poc_recipe() if hasattr(state, "get_poc_recipe") else (_meta.get("poc_recipe") or {})

        has_any_content = (
            bool(recipe.get("trigger_mutations"))
            or bool(recipe.get("carrier"))
            or bool(recipe.get("open_gaps"))
            or bool(gates)
            or bool(getattr(state, "active_input_mappings", None))
            or bool(_brief_sections.get("requirements"))
            or bool(_code_ctx)
        )

        if not has_any_content:
            return (
                "## Required Conditions\n"
                "- Pending: no PoC-relevant conditions have been extracted yet. "
                "[source: unset]"
            )

        lines = ["## Required Conditions"]

        # --- Fix A: Hard contract slots (objectives, fields, recipe gaps, selectors first) ---
        from ..core.runtime_context_contract import render_context_contract_slots
        contract = render_context_contract_slots(state)
        conditions_slots = contract.get("conditions", [])
        if conditions_slots:
            lines.extend(conditions_slots)
            lines.append("")

        idx = 0
        seen_conditions: set = set()

        # --- 1. Concrete mutation targets from recipe ---
        mutations = recipe.get("trigger_mutations", [])
        if mutations:
            for m in mutations[:4]:
                mid = m.get("mapping_id", "")
                if mid and mid in seen_conditions:
                    continue
                arg_role = m.get("argument_role", "")
                offset = m.get("offset")
                width = m.get("width")
                strategy = m.get("value_strategy", "")
                constraint = m.get("constraint", "")
                evidence = m.get("evidence", "")
                idx += 1
                if offset is not None and width:
                    target = f"offset=0x{offset:X} width={width}"
                elif offset is not None:
                    target = f"offset=0x{offset:X}"
                else:
                    target = "offset=unresolved"
                role_text = f" ({arg_role})" if arg_role else ""
                strat_text = f" → {strategy}" if strategy else ""
                line = f"{idx}. **Mutate** {target}{role_text}{strat_text}"
                if constraint:
                    line += f"; {constraint}"
                if evidence:
                    line += f" [{evidence}]"
                lines.append(line)
                if mid:
                    seen_conditions.add(mid)

        # --- 2. Carrier / seed strategy ---
        carrier = recipe.get("carrier", {})
        if isinstance(carrier, dict) and carrier:
            strategy = carrier.get("strategy", "")
            seed = carrier.get("seed_path", "")
            reason = carrier.get("reason", "")
            if strategy:
                idx += 1
                line = f"{idx}. **Carrier**: {strategy}"
                if seed:
                    line += f"; seed={seed}"
                if reason:
                    line += f" ({reason})"
                lines.append(line)

        # --- 3. PoC sanity / carrier checks ---
        sanity = _meta.get("last_poc_sanity") or {}
        if isinstance(sanity, dict) and sanity:
            sanity_text = IRRenderer.render_poc_sanity(sanity)
            if sanity_text:
                for sline in sanity_text.split("\n")[:3]:
                    idx += 1
                    lines.append(f"{idx}. {sline}")

        # --- 4. Input mappings ---
        for mapping in list(getattr(state, "active_input_mappings", []) or [])[:4]:
            mapping_id = str(mapping.get("mapping_id") or "")
            if mapping_id and mapping_id in seen_conditions:
                continue
            rendered = IRRenderer.render_input_mapping(mapping)
            for rline in rendered.splitlines():
                idx += 1
                lines.append(f"{idx}. {rline}" if rline.startswith("[") else f"   {rline}")
            if mapping_id:
                seen_conditions.add(mapping_id)
            if idx >= 8:
                break

        def _is_nonsensical(cond: str) -> bool:
            """Filter out obviously useless conditions like '8.0 == 0'."""
            stripped = cond.strip()
            if _re.match(r'^-?\d+(?:\.\d+)?\s*==\s*-?\d+(?:\.\d+)?$', stripped):
                return True
            if _re.match(r'^-?\d+(?:\.\d+)?\s*!=\s*-?\d+(?:\.\d+)?$', stripped):
                return True
            return False

        # --- 5. Confirmed gates ---
        for g in state.confirmed_gates():
            cond = str(g.required_condition or g.description or "").strip()
            if not cond or cond in seen_conditions or _is_nonsensical(cond):
                continue
            seen_conditions.add(cond)
            idx += 1
            source = "code reading" if g.status == "confirmed" else "inferred"
            lines.append(f"{idx}. [✓ {g.gate_type}] {cond} — source: {source}")

        # --- 5b. Open/inferred gates ---
        for g in state.open_gates():
            cond = str(g.required_condition or g.description or "").strip()
            if not cond or cond in seen_conditions or _is_nonsensical(cond):
                continue
            seen_conditions.add(cond)
            idx += 1
            evidence = str(g.evidence or "").strip()
            source_hint = f" ({evidence[:60]})" if evidence else ""
            lines.append(f"{idx}. [? {g.gate_type}] {cond} — source: inferred{source_hint}")

        # --- 6. Trigger conditions from analysis brief ---
        reqs_text = str(_brief_sections.get("requirements", "") or "").strip()
        if reqs_text and not gates:
            for req_line in reqs_text.split("\n"):
                req_line = req_line.strip()
                if not req_line or req_line in seen_conditions or _is_nonsensical(req_line):
                    continue
                seen_conditions.add(req_line)
                idx += 1
                lines.append(f"{idx}. [? analysis] {req_line} [source: analysis service]")

        triggers_text = str(_brief_sections.get("triggers", "") or "").strip()
        if triggers_text and not gates:
            for trig_line in triggers_text.split("\n"):
                trig_line = trig_line.strip()
                if not trig_line or trig_line in seen_conditions or _is_nonsensical(trig_line):
                    continue
                seen_conditions.add(trig_line)
                idx += 1
                lines.append(f"{idx}. [? trigger] {trig_line} [source: analysis service]")

        # --- 7. Open mapping gaps ---
        gaps = recipe.get("open_gaps", [])
        if gaps:
            for g in gaps[:3]:
                if g not in seen_conditions:
                    idx += 1
                    lines.append(f"{idx}. [gap] {g}")
                    seen_conditions.add(g)

        # --- 8. Refuted conditions ---
        for g in state.refuted_gates()[-2:]:
            cond = str(g.required_condition or g.description or "").strip()
            if not cond:
                continue
            idx += 1
            repair = str(g.repair_hint or "").strip()
            repair_hint = f" → try: {repair}" if repair else ""
            lines.append(f"{idx}. [✗ {g.gate_type}] {cond}{repair_hint}")

        # Condition snippets are now in hard contract slots (Fix A)

        if idx == 0:
            # Provide structured gap instead of "non-actionable"
            primary = state._primary_sink_id() if hasattr(state, "_primary_sink_id") else ""
            if primary:
                return (
                    "## Required Conditions\n"
                    f"- ? Mapping gap: identify concrete input fields controlling {primary} arguments.\n"
                    f"- Next: READ sink code to find parser-read → sink-argument data flow, "
                    f"then record input mapping."
                )
            return (
                "## Required Conditions\n"
                "- Pending: no PoC-relevant conditions have been extracted yet. "
                "[source: unset]"
            )

        # Cap total conditions at 12
        MAX_CONDITIONS = 12
        if idx > MAX_CONDITIONS:
            omitted = idx - MAX_CONDITIONS
            content_lines = lines[1:]
            lines = [lines[0]] + content_lines[:MAX_CONDITIONS]
            lines.append(f"... ({omitted} more conditions omitted)")

        # Emphasize first open gate as primary blocker
        first_open = state.first_open_gate()
        if first_open:
            cond = str(first_open.required_condition or first_open.description or "").strip()
            if cond:
                lines.append("")
                lines.append(f"**Primary blocker**: {cond}")

        # Numerical constraints
        numerical = state.derive_numerical_constraints()
        if numerical:
            lines.append("")
            lines.append("**Numerical constraints**: " + "; ".join(numerical[:6]))

        # Code context
        if _code_ctx:
            guard_lines = [l for l in _code_ctx.split("\n") if "Guard:" in l or "Risk:" in l]
            if guard_lines:
                lines.append("")
                lines.append("**Code analysis**:")
                lines.extend(guard_lines[:4])

        # Analyzer diagnostics
        diagnostics = list(getattr(state, "constraint_diagnostics", []) or [])[-3:]
        if diagnostics:
            lines.append("")
            for item in diagnostics:
                source_tag = "[FEEDBACK]" if item.get("source") == "feedback" else "[ANALYZER]"
                lines.append(
                    f"- {source_tag} [{str(item.get('severity', 'info')).upper()}] "
                    f"{item.get('code', 'analysis')}: {item.get('message', '')}"
                )

        return "\n".join(lines)

    @staticmethod
    def _render_experiments(state: CyberGymState) -> str:
        """Section 5: Experiments — PoC attempts with differential analysis."""
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")

        # Hidden during ingestion and exploration (no submissions yet)
        poc_attempts = int(getattr(state, "poc_attempts", 0) or 0)
        if poc_attempts == 0 or phase in ("ingestion", "exploration"):
            return (
                "## Experiments\n"
                "- No PoC submissions yet. [source: runtime state]"
            )

        lines = ["## Experiments"]

        # --- Fix A: Hard contract slots (feedback action, negative evidence, sanity first) ---
        from ..core.runtime_context_contract import render_context_contract_slots
        contract = render_context_contract_slots(state)
        experiment_slots = contract.get("experiments", [])
        if experiment_slots:
            lines.extend(experiment_slots)
            lines.append("")

        # Attempt count and consecutive misses
        consecutive = int(getattr(state, "consecutive_misses", 0) or 0)
        if consecutive > 0:
            lines.append(f"({poc_attempts} attempts, {consecutive} consecutive no-crash)")
        else:
            lines.append(f"({poc_attempts} attempts)")

        # Render feedback as a table
        trimmed = state.hot_feedback_window[-3:]
        if trimmed:
            lines.append("")
            lines.append("| # | PoC | Result | Key insight |")
            lines.append("|---|-----|--------|------------|")
            for i, item in enumerate(trimmed, 1):
                poc_path = str(getattr(item, "poc_path", "") or "")
                poc_name = poc_path.split("/")[-1] if poc_path else "?"
                output = str(getattr(item, "output", "") or "").strip()
                # Extract key info from output
                size_match = ""
                if "Reading" in output and "bytes" in output:
                    m = _re.search(r"Reading (\d+) bytes", output)
                    if m:
                        size_match = f" ({m.group(1)}B)"
                exit_code = getattr(item, "exit_code", 0)
                if exit_code != 0 and exit_code is not None:
                    result = "CRASH"
                else:
                    # Check assessment
                    assessment = str(getattr(item, "assessment", "") or "").strip()
                    if (
                        "no_trigger" in assessment
                        or "no_crash_unknown" in assessment
                        or "no_trigger" in output.lower()
                    ):
                        result = "NO_CRASH"
                    elif "triggered" in assessment.lower():
                        result = "TRIGGERED"
                    else:
                        result = "NO_CRASH"
                # Key insight: extract from suggested_action or gate info
                suggested = str(getattr(item, "suggested_action", "") or "").strip()
                insight = suggested[:80] if suggested else "Execution successful, no crash"
                lines.append(f"| {i} | {poc_name}{size_match} | {result} | {insight} |")

        # Pattern analysis if consecutive misses >= 3
        if consecutive >= 3:
            lines.append("")
            lines.append(
                f"**Pattern**: {consecutive} consecutive no-crash submissions. "
                "Classify the miss before more variants: path not reached vs. path reached "
                "but trigger condition unmet."
            )

        # Negative evidence impact display
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
                avoid = str(ev.get("avoid_next", ""))[:60]
                ttl = ev.get("ttl", 0)
                entry = f"- [{kind}] {summary} (ttl={ttl})"
                if avoid:
                    entry += f" | avoid: {avoid}"
                lines.append(entry)

        # Carrier sanity context
        sanity = (state.metadata or {}).get("last_poc_sanity", {}) if isinstance(state.metadata, dict) else {}
        if sanity and not sanity.get("passed", True):
            lines.append("")
            lines.append(f"**Carrier sanity**: FAIL — {sanity.get('summary', 'see above')}")

        # Crash stack from ASAN output
        crash_stack = str(getattr(state, "crash_stack", "") or "").strip()
        if crash_stack:
            lines.append("")
            lines.append(f"**Crash stack**: {crash_stack}")

        # Experiment snippets are now in hard contract slots (Fix A)

        return "\n".join(lines)

    @staticmethod
    def _render_next_action(state: CyberGymState) -> str:
        """Section 6: Next Action — generated from the current blocking gap.

        Fix A: If the runtime contract produces a required action slot,
        that slot is authoritative.  Only one required action is emitted;
        if a runtime block exists, SUBMIT NOW is forbidden.
        """
        lines = ["## Next Action"]

        # --- Fix A: Use hard contract slot as the single source of truth ---
        from ..core.runtime_context_contract import render_context_contract_slots
        contract = render_context_contract_slots(state)
        next_action_slots = contract.get("next_action", [])
        if next_action_slots:
            lines.extend(next_action_slots)
            return "\n".join(lines)

        # Check for special states first
        if getattr(state, "pending_reflection", False):
            lines.append("**Required**: `record_reflection(summary, next_step)`")
            lines.append("- Do not submit PoCs or read code before recording a reflection.")
            return "\n".join(lines)

        if getattr(state, "pending_sink_checkpoint", False):
            lines.append("**CHECKPOINT**: record_sink_candidate required before proceeding")
            lines.append("")
            lines.append(
                "**Recommended**: Record the sink function you identified with evidence."
            )
            lines.append(
                '  - Command: `record_sink_candidate("function_name", "evidence from code reading", location="file:line")`'
            )
            lines.append("  - After recording: BASH/WRITE/submit_poc unlock")
            return "\n".join(lines)

        # Check for ready PoCs
        ready_paths = []
        for item in list(getattr(state, "ready_pocs", []) or []):
            if getattr(item, "file_path", "") and getattr(item, "ready_to_submit", True):
                ready_paths.append(item.file_path)

        # Priority 0: carrier sanity repair
        sanity = (state.metadata or {}).get("last_poc_sanity", {}) if isinstance(state.metadata, dict) else {}
        if sanity and not sanity.get("passed", True):
            issues = sanity.get("issues", [])
            repair = ""
            for issue in (issues or []):
                if issue.get("severity") == "fail" and issue.get("repair_hint"):
                    repair = issue["repair_hint"]
                    break
            lines.append("**Required**: Fix carrier sanity failure before submitting.")
            lines.append(f"- Issue: {sanity.get('summary', 'carrier structure invalid')}")
            if repair:
                lines.append(f"- Repair: {repair}")
            lines.append("- This is a carrier format problem, NOT a sink/path problem. Do not change your sink candidate.")
            return "\n".join(lines)

        # Hard blockers are now handled by the contract slot above (Fix A)

        # Priority 1: submit ready PoC — BUT check negative evidence first
        if ready_paths:
            negative_evidence = list(
                (state.metadata or {}).get("negative_evidence", [])
                if isinstance(state.metadata, dict) else []
            )
            # Check if this family has repeated no-trigger evidence
            family_id = ""
            if state.ready_pocs:
                family_id = str(getattr(state.ready_pocs[0], "family_id", "") or "")
            same_family_no_trigger = [
                ev for ev in negative_evidence
                if ev.get("family_id") == family_id
                and ev.get("kind") in ("path_reached_no_trigger", "no_crash_unknown")
                and ev.get("ttl", 0) > 0
            ] if family_id else []
            if len(same_family_no_trigger) >= 3:
                lines.append("**Replan recommended**: 3+ no-trigger evidence for this family.")
                lines.append(f"- Submit `{ready_paths[0]}` anyway, OR revise mutation strategy first.")
                lines.append("- Consider: rotating to a different sink candidate or revising the mutation offset/value.")
                return "\n".join(lines)
            lines.append(f"**SUBMIT NOW**: `submit_poc(\"{ready_paths[0]}\")`")
            if len(ready_paths) > 1:
                lines.append(f"- Submit all {len(ready_paths)} ready PoCs in this step.")
            return "\n".join(lines)

        # General case: derive from first_open_gate
        first_open = state.first_open_gate()
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")
        consecutive = int(getattr(state, "consecutive_misses", 0) or 0)
        _meta = getattr(state, "metadata", {}) or {}
        _nav_leads = list(_meta.get("_code_context_nav_leads", []) or [])
        desc_analysis = getattr(state, "description_analysis", None)
        desc_status = str(getattr(desc_analysis, "status", "") or "pending")

        if phase == "ingestion" and desc_status == "pending":
            lines.append("**Blocking gap**: description.txt has not been converted into structured navigation priors.")
            lines.append("")
            lines.append("**Recommended**: call `analyze_description(...)` with vulnerability class, access mode, mechanism tags, suspect names/files, numeric facts, and trigger conditions.")
            lines.append("- Stop condition: `description_analysis.status` becomes recorded/verified; do not confirm a sink from description alone.")
            return "\n".join(lines)

        ranked_paths = list(getattr(state, "ranked_vulnerability_paths", []) or [])
        active_path_id = str(getattr(state, "selected_analysis_path_id", "") or "")
        if ranked_paths and not state.confirmed_sink_candidates() and not active_path_id:
            path = ranked_paths[0]
            next_read = path.get("next_read") or {}
            nr_path = str(next_read.get("path") or "")
            nr_offset = int(next_read.get("offset", 0) or 0)
            nr_limit = int(next_read.get("limit", 160) or 160)
            endpoint = path.get("endpoint") or {}
            if nr_path:
                lines.append("**Blocking gap**: Top ranked vulnerability path has not been verified by reading the endpoint.")
                lines.append("")
                lines.append(
                    f"**Recommended**: `READ(path=\"{nr_path}\", offset={nr_offset}, limit={nr_limit})`"
                )
                lines.append(
                    f"- Target: `{endpoint.get('function', 'endpoint')}` path_id={path.get('path_id')} [source: analysis service]"
                )
                role = str(path.get("endpoint_role") or "unknown")
                path_id = str(path.get("path_id") or "")
                lines.append(
                    "- Stop condition: confirm/reject endpoint role, then call "
                    f"`record_sink_candidate(function, evidence, candidate_role=\"{role}\", ranked_path_id=\"{path_id}\")` "
                    "with code evidence."
                )
                return "\n".join(lines)

        verified_refs = list(getattr(state, "verified_search_refs", []) or [])
        if verified_refs and not state.confirmed_sink_candidates():
            ref = verified_refs[0]
            target_path = str(getattr(ref, "file", "") or "")
            line_no = int(getattr(ref, "line", 0) or 0)
            symbol = str(getattr(ref, "symbol", "") or getattr(ref, "query", "") or "verified ref")
            if target_path:
                offset = max(0, line_no - 40) if line_no else 0
                lines.append("**Blocking gap**: verified description reference has not been read or classified as caller/sink/path anchor.")
                lines.append("")
                lines.append(
                    f"**Recommended**: `READ(path=\"{target_path}\", offset={offset}, limit=160)`"
                )
                lines.append(f"- Target: `{symbol}` [source: analysis service]")
                lines.append("- Stop condition: decide whether this code is crash_site, causal_site, path_anchor, or only a caller.")
                return "\n".join(lines)

        consumption = getattr(getattr(state, "input_format", None), "consumption", None)
        if consumption and getattr(consumption, "status", "") in {"partial", "unresolved"}:
            resolution = getattr(state, "harness_resolution", None)
            selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
            selected = next(
                (item for item in list(getattr(state, "harness_candidates", []) or [])
                 if item.candidate_id == selected_id),
                None,
            )
            if selected and selected.source_path:
                offset = max(0, int(getattr(selected, "line", 1) or 1) - 20)
                lines.append("**Blocking gap**: selected harness consumption is partial; first-hop or dispatch evidence is unresolved.")
                lines.append("")
                lines.append(
                    f"**Recommended**: `READ(path=\"{selected.source_path}\", offset={offset}, limit=160)`"
                )
                lines.append("- Stop condition: identify data/size delivery, magic/selector gates, and direct first-hop callees.")
                return "\n".join(lines)

        unresolved_mappings = [
            item for item in list(getattr(state, "active_input_mappings", []) or [])
            if item.get("status") == "unresolved"
        ]
        if unresolved_mappings and state.confirmed_sink_candidates():
            mapping = unresolved_mappings[0]
            expr = str(mapping.get("sink_expression") or mapping.get("sink_argument") or "critical argument")
            lines.append("**Blocking gap**: critical sink argument is not mapped to input bytes yet.")
            lines.append("")
            lines.append(f"**Recommended**: trace or READ the definition for `{expr}`.")
            lines.append("- Stop condition: prove offset/width/alias, or keep it explicitly symbolic and proceed with a candidate.")
            return "\n".join(lines)

        if first_open:
            cond = str(first_open.required_condition or first_open.description or "").strip()
            lines.append(f"**Blocking gap**: {cond}")
            lines.append("")

            # Generate recommendation based on gate type
            gate_type = first_open.gate_type
            if gate_type == "format_gate":
                lines.append("**Recommended**: READ the parser entry to confirm input format.")
                lines.append("  - Stop condition: Confirmed or refuted format requirement")
            elif gate_type == "bounds_gate":
                lines.append("**Recommended**: READ the vulnerable function to find buffer size.")
                lines.append("  - Stop condition: Concrete buffer size or overflow threshold")
            elif gate_type == "value_gate":
                lines.append("**Recommended**: READ or GREP for the specific value constraint.")
                lines.append("  - Stop condition: Confirmed or refuted value requirement")
            elif gate_type == "dispatch_gate":
                lines.append("**Recommended**: CallsiteSearch to trace call chain from entry to sink.")
                lines.append("  - Stop condition: Understood dispatch path")
            else:
                lines.append(f"**Recommended**: Resolve this {gate_type} condition.")
                lines.append("  - Stop condition: Confirmed or refuted condition")

            # Add specific READ target from code context nav leads
            if _nav_leads:
                lead = _nav_leads[0]
                target_path = lead.get("path", "")
                if target_path:
                    lines.append(f"  - Target: `{target_path}`")
                    offset = lead.get("offset", "")
                    limit = lead.get("limit", "")
                    if offset or limit:
                        lines.append(f"    offset={offset} limit={limit}")
                    why = lead.get("why", "")
                    if why:
                        lines.append(f"    why: {why}")
        else:
            # No open gates: either record a sink, or convert confirmed evidence into a PoC.
            confirmed_sinks = list(state.confirmed_sink_candidates())
            if confirmed_sinks:
                active_id = str(getattr(state, "active_sink_candidate_id", "") or "")
                sink = next((item for item in confirmed_sinks if item.candidate_id == active_id), confirmed_sinks[0])
                confirmed_gates = [
                    gate for gate in list(getattr(state, "call_chain_gates", []) or [])
                    if getattr(gate, "status", "") == "confirmed"
                ]
                lines.append("**Blocking gap**: a sink candidate is confirmed, but no ready PoC has been written or submitted.")
                lines.append("")
                if confirmed_gates:
                    gate_text = str(
                        getattr(confirmed_gates[0], "required_condition", "")
                        or getattr(confirmed_gates[0], "description", "")
                        or "the first confirmed Required Condition"
                    ).strip()
                    lines.append(
                        f"**Recommended**: write a candidate PoC for `{sink.function}` that satisfies "
                        f"`{gate_text}`; then call `submit_poc`."
                    )
                else:
                    lines.append(
                        f"**Recommended**: READ `{sink.function}` or its immediate caller to extract one "
                        "trigger condition, then write and submit a minimal PoC."
                    )
                lines.append("- Stop condition: `submit_poc` is called, or a missing gate/input mapping is recorded.")
            elif phase in ("exploration", "investigation"):
                lines.append("**Blocking gap**: no source-backed sink candidate has been confirmed yet.")
                lines.append("")
                lines.append("**Recommended**: READ/GREP/CallsiteSearch the most suspicious parser or endpoint, then call `record_sink_candidate(function, evidence, location?, confidence?)`.")
                lines.append("- Stop condition: one candidate crash-site or causal leaf function is recorded with code evidence.")
            else:
                lines.append("**Blocking gap**: no source-backed sink candidate or open gate is available.")
                lines.append("")
                lines.append("**Recommended**: structure the description or READ the highest-confidence code anchor to identify a sink candidate.")
                lines.append("- Stop condition: one source-backed sink candidate is recorded, or a ranked path endpoint is rejected.")
            # Suggest READ target from navigation leads if available
            if _nav_leads:
                lead = _nav_leads[0]
                target_path = lead.get("path", "")
                if target_path:
                    lines.append(f"  - Suggested read: `{target_path}`")

        # Warn against submitting more variants if stuck
        if consecutive >= 3:
            lines.append("")
            lines.append(
                "**Not recommended**: Submitting more PoC variants without resolving the blocking gap. "
                f"({consecutive} consecutive no-crash)"
            )

        return "\n".join(lines)

    @staticmethod
    def _render_phase_tools(state: CyberGymState) -> List[str]:
        """Phase-adaptive compact tool list.

        Replaces the old 15-line full tool list with 3-5 lines
        appropriate for the current phase.  Checkpoint overrides
        still take priority.
        """
        phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")

        # Checkpoint overrides take priority
        if getattr(state, "pending_reflection", False):
            return [
                "- `record_reflection(summary, next_step, request_reinvestigation?)` — record one concise reflection now.",
                "- Do not call `READ`, `GREP`, `BASH`, edit tools, or `submit_poc` before `record_reflection`.",
            ]

        if getattr(state, "pending_sink_checkpoint", False):
            return [
                "- `record_sink_candidate(function, evidence, location?, confidence?)` — STRONGLY RECOMMENDED before proceeding.",
                "- `READ` / `GREP` / `FindSymbols` / `CallsiteSearch` — only if needed to identify the sink function.",
                "- Do not call `submit_poc`, `WRITE`, `BASH`, or edit tools until the checkpoint is satisfied.",
            ]

        # Ready PoCs — submit only
        ready_paths = []
        for item in list(getattr(state, "ready_pocs", []) or []):
            if getattr(item, "file_path", "") and getattr(item, "ready_to_submit", True):
                ready_paths.append(item.file_path)
        if ready_paths:
            lines = [f"- `submit_poc(poc_path)` — submit now: {', '.join(f'`{p}`' for p in ready_paths[:3])}"]
            lines.append("- `record_reflection` only if explicitly required.")
            return lines

        # Phase-specific tool sets
        if phase == "ingestion":
            return [
                "- `analyze_description(...)` — record structured priors from description.txt",
                "- `READ` / `RepoMap` / `FindSymbols` — verify description refs and harness entry",
                "- `set_crash_type(crash_type)` — legacy fallback only; submit_poc feedback overrides it",
            ]
        elif phase == "exploration":
            return [
                "- `READ` / `GREP` / `CallsiteSearch` / `FindSymbols` — trace code paths to sink",
                "- `record_sink_candidate(function, evidence)` — record identified sink",
                "- `record_chain_node(function, location, role, description, status)` — record call chain",
            ]
        elif phase == "investigation":
            return [
                "- `READ` / `CallsiteSearch` / `GREP` — verify call chain and constraints",
                "- `record_chain_node` / `record_gate(node_function, gate_type, description, required_condition, status)` — record path constraints",
                "- `record_sink_candidate` — if new sink identified",
            ]
        elif phase == "formulation":
            return [
                "- `READ` / `GREP` — confirm remaining conditions",
                "- `BASH(command)` / `WRITE(path, content)` — write PoC files",
                "- `submit_poc(poc_path)` — submit ready PoCs",
                "- `CorpusInspect` / `HexView` — inspect seed files before constructing candidates",
            ]
        elif phase == "verification":
            return [
                "- `READ` / `GREP` — analyze verification feedback",
                "- `BASH` / `WRITE` — revise PoC",
                "- `submit_poc` — resubmit revised PoC",
                "- `record_reflection` — capture learnings from failed attempts",
            ]
        else:
            return [
                "- `READ` / `GREP` / `BASH` / `WRITE` / `submit_poc`",
            ]
