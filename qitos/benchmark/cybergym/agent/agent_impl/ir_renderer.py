"""IR → Markdown rendering for analysis service results.

Converts structured analysis IR dicts (requirements, paths, gaps, etc.)
into human-readable Markdown for the LLM observation context.

Replaces html.escape(str(dict)) rendering that produced unreadable
output like &#x27;requirement_id&#x27;: &#x27;req_abc&#x27;.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class IRRenderer:
    """Pure-function renderer for analysis IR dicts.

    No state access — all methods are static and take explicit data.
    """

    # ------------------------------------------------------------------
    # 1. Requirement rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_requirement(req: Dict[str, Any]) -> str:
        """Render a single requirement/constraint IR dict as readable Markdown.

        Input shape (from analysis/service.py _requirement_from_constraint):
        {
            "requirement_id": "req_...",
            "path_id": str,
            "order": int,
            "role": "reachability" | "trigger" | "hazard" | ...,
            "gate_type": "path_gate" | "value_gate" | ...,
            "expression": str,
            "safe_formula": str,
            "violation_formula": str,
            "input_mapping": str,
            "status": str,
            "confidence": float,
            "origin": dict,
            "reason": str,
        }
        """
        role = req.get("role", "reachability")
        gate_type = req.get("gate_type", "unknown")
        expression = str(req.get("expression") or "").strip()
        reason = str(req.get("reason") or "").strip()
        safe = str(req.get("safe_formula") or "").strip()
        violation = str(req.get("violation_formula") or "").strip()
        input_map = str(req.get("input_mapping") or "").strip()
        confidence = float(req.get("confidence", 0.5) or 0.5)
        status = str(req.get("status", "inferred") or "inferred")

        conf_tag = (
            "high" if confidence >= 0.7
            else "medium" if confidence >= 0.4
            else "low"
        )

        # Status marker
        status_mark = {
            "confirmed": "[confirmed]",
            "refuted": "[refuted]",
        }.get(status, "")

        parts: List[str] = []

        # Header line: role gate_type conf=... status
        header = f"[{role} {gate_type} conf={conf_tag}]"
        if status_mark:
            header += f" {status_mark}"
        parts.append(header)

        # Condition
        if expression:
            parts.append(f"  condition: {expression}")

        # Reason / why
        if reason:
            parts.append(f"  why: {reason}")

        # Safe invariant (what the PoC must satisfy)
        if safe:
            parts.append(f"  safe: {safe}")

        # Violation formula (what triggers the bug)
        if violation:
            parts.append(f"  trigger: {violation}")

        # Input mapping (how to set this in the PoC)
        if input_map:
            parts.append(f"  input: {input_map}")

        # Origin (source location)
        origin = req.get("origin") or {}
        origin_str = _format_origin(origin)
        if origin_str:
            parts.append(f"  source: {origin_str}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 2. Path rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_path(path: Dict[str, Any]) -> str:
        """Render a candidate call path as a readable chain.

        Input shape (from find_paths_to_target, serialized AnalysisPath):
        {
            "path_id": "path_...",
            "symbol_ids": list[str],
            "edges": list[dict],      # CallEdge dicts
            "constraints": list[dict],
            "score": float,
            "has_contradiction": bool,
            "contradictions": list[str],
        }
        """
        path_id = path.get("path_id", "")
        symbol_ids = path.get("symbol_ids") or []
        edges = path.get("edges") or []
        score = float(path.get("score", 0) or 0)
        has_contra = bool(path.get("has_contradiction"))

        # Build chain from edges if available, otherwise from symbol_ids
        if edges:
            chain_parts = []
            for edge in edges:
                caller = _short_symbol(edge.get("caller_id", ""))
                callee = _short_symbol(edge.get("callee_id", ""))
                if caller and caller not in [p for p in chain_parts]:
                    chain_parts.append(caller)
                if callee:
                    chain_parts.append(callee)
        elif symbol_ids:
            chain_parts = [_short_symbol(sid) for sid in symbol_ids]
        else:
            chain_parts = ["???"]

        # Build chain string
        chain_str = " → ".join(chain_parts) if chain_parts else "??? (empty path)"

        # Score and contradiction info
        score_str = f" (score={score:.2f})" if score > 0 else ""
        contra_str = " [CONTRADICTION]" if has_contra else ""

        return f"Path `{path_id}`: {chain_str}{score_str}{contra_str}"

    # ------------------------------------------------------------------
    # 3. Gap rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_gap(gap: Dict[str, Any]) -> str:
        """Render a gap in analysis coverage.

        Input shape:
        {
            "id": "target_resolution_required" | "caller_path_required" | ...,
            "reason": str,
            "candidate_symbol_ids": list[str],
            "next_query": {"tool": str, "arguments": dict},
        }
        """
        gap_id = str(gap.get("id") or "").strip()
        reason = str(gap.get("reason") or "").strip()
        candidates = gap.get("candidate_symbol_ids") or []
        next_query = gap.get("next_query") or {}

        parts: List[str] = []
        if gap_id:
            parts.append(f"Gap: {gap_id}")
        if reason:
            parts.append(f"  reason: {reason}")
        if candidates:
            # Show at most 5 candidate symbols
            names = [_short_symbol(c) for c in candidates[:5]]
            suffix = f" (+{len(candidates) - 5} more)" if len(candidates) > 5 else ""
            parts.append(f"  candidates: {', '.join(names)}{suffix}")
        if next_query:
            tool = next_query.get("tool", "")
            args = next_query.get("arguments") or {}
            if tool:
                arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                parts.append(f"  next: {tool}({arg_str})" if arg_str else f"  next: {tool}()")

        return "\n".join(parts) if parts else "Gap: (unspecified)"

    # ------------------------------------------------------------------
    # 4. Target rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_target(target: Dict[str, Any]) -> str:
        """Render a sink target resolution dict.

        Input shape:
        {
            "status": "unresolved" | "resolved" | ...,
            "requested": {...},
            "reason": str,
            "candidate_symbol_ids": list[str],
            "evidence": list,
        }
        """
        status = str(target.get("status") or "").strip()
        reason = str(target.get("reason") or "").strip()
        requested = target.get("requested") or {}
        func = str(requested.get("function") or "").strip()
        file = str(requested.get("file") or "").strip()
        conf = float(requested.get("agent_confidence", 0) or 0)

        parts: List[str] = []
        if func:
            loc = f" @{file}" if file else ""
            parts.append(f"Target: `{func}`{loc}")
        if status:
            parts.append(f"  status: {status}")
        if reason:
            parts.append(f"  reason: {reason}")
        if conf > 0:
            parts.append(f"  confidence: {conf:.2f}")

        # Description-derived flag
        if requested.get("description_derived"):
            parts.append("  source: description-derived (may be inaccurate)")

        return "\n".join(parts) if parts else "Target: (unspecified)"

    # ------------------------------------------------------------------
    # 5. Description-reference rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_verified_ref(ref: Any) -> str:
        """Render one source-backed match derived from description analysis.

        The rendered line is intentionally explicit about provenance: a
        verified ref proves that a name/literal exists in source, not that it is
        the crash sink.
        """
        query = _clean_inline(getattr(ref, "query", "") if not isinstance(ref, dict) else ref.get("query", ""))
        symbol = _clean_inline(getattr(ref, "symbol", "") if not isinstance(ref, dict) else ref.get("symbol", ""))
        file = _clean_inline(getattr(ref, "file", "") if not isinstance(ref, dict) else ref.get("file", ""))
        line = getattr(ref, "line", 0) if not isinstance(ref, dict) else ref.get("line", 0)
        match_kind = _clean_inline(getattr(ref, "match_kind", "") if not isinstance(ref, dict) else ref.get("match_kind", ""))
        confidence = getattr(ref, "confidence", 0.0) if not isinstance(ref, dict) else ref.get("confidence", 0.0)
        target = symbol or file or "source match"
        loc = ""
        if file:
            loc = f" @{file}"
            try:
                if int(line or 0) > 0:
                    loc += f":{int(line)}"
            except Exception:
                pass
        conf = ""
        try:
            if float(confidence or 0) > 0:
                conf = f", conf={float(confidence):.2f}"
        except Exception:
            pass
        kind = f" {match_kind}" if match_kind else ""
        q = f"`{query}` -> " if query else ""
        return f"- {q}`{target}`{loc} [{kind.strip() or 'match'}; source: analysis service{conf}]"

    @staticmethod
    def render_unresolved_hint(hint: Any) -> str:
        """Render one unresolved description hint without implying absence."""
        text = _clean_inline(str(hint or ""))
        return (
            f"- `{text}` [source: unresolved description hint; "
            "not negative evidence]"
        ) if text else "- (empty unresolved hint) [source: unresolved description hint; not negative evidence]"

    @staticmethod
    def render_harness_consumption(model: Any, *, mode: str = "summary", max_evidence: int = 3) -> str:
        """Render selected-harness consumption model without raw dataclass text."""
        if model is None:
            return ""
        patterns = list(getattr(model, "patterns", []) or [])
        pattern = "+".join(p for p in patterns if p and p != "unknown") or str(getattr(model, "pattern", "") or "unknown")
        first_hops = list(getattr(model, "first_hops", []) or [])
        magic = _clean_inline(getattr(model, "magic_bytes", ""))
        selector = _clean_inline(getattr(model, "selector_expression", ""))
        status = _clean_inline(getattr(model, "status", ""))
        if mode == "summary":
            parts = []
            if pattern and pattern != "unknown":
                parts.append(f"Pattern: {pattern}")
            if first_hops:
                parts.append("First hops: " + ", ".join(f"`{_clean_inline(h, 80)}`" for h in first_hops[:3]))
            if selector:
                parts.append(f"Selector: `{selector}`")
            if magic:
                parts.append(f"Magic: `{magic}`")
            if status and status != "success":
                parts.append(f"Consumption: {status}")
            return " | ".join(parts)

        lines: List[str] = []
        for ev in list(getattr(model, "evidence", []) or [])[:max_evidence]:
            kind = _clean_inline(getattr(ev, "kind", ""))
            expr = _clean_inline(getattr(ev, "expression", ""))
            file = _clean_inline(getattr(ev, "file", ""))
            line = getattr(ev, "line", 0)
            loc = f" @{file}" if file else ""
            try:
                if int(line or 0) > 0:
                    loc += f":{int(line)}"
            except Exception:
                pass
            conf = getattr(ev, "confidence", 0.0)
            try:
                conf_text = f", conf={float(conf):.2f}" if float(conf or 0) > 0 else ""
            except Exception:
                conf_text = ""
            if kind or expr:
                lines.append(f"- {kind or 'evidence'}: {expr}{loc} [source: harness AST{conf_text}]")
        return "\n".join(lines)

    @staticmethod
    def render_ranked_path(path: Dict[str, Any], index: int = 1, *, active: bool = False) -> str:
        """Render one RankedVulnerabilityPath as compact path guidance."""
        score = float(path.get("score", 0.0) or 0.0)
        status = str(path.get("resolution_status") or "")
        chain = list(path.get("chain") or [])
        endpoint = path.get("endpoint") or {}
        signal = endpoint.get("signal") or {}
        chain_names = [_clean_inline(item.get("function", ""), 80).split("::")[-1] for item in chain]
        if len(chain_names) > 6:
            chain_names = [chain_names[0], chain_names[1], "...", chain_names[-2], chain_names[-1]]
        chain_text = " -> ".join(name for name in chain_names if name) or _clean_inline(endpoint.get("function", "endpoint"))
        active_text = " ◀ ACTIVE" if active else " recommended, not confirmed" if index == 1 else ""
        partial = f" [partial: {status}]" if status and status != "resolved" else ""
        role = _clean_inline(path.get("endpoint_role", ""))
        family = _clean_inline(path.get("candidate_family", ""))
        file = _clean_inline(endpoint.get("file", ""))
        line = endpoint.get("line", 0)
        kind = _clean_inline(signal.get("kind", ""))
        expr = _clean_inline(signal.get("expression", ""))
        breakdown = path.get("score_breakdown") or {}
        evidence = " ".join(
            f"{key}={float(breakdown.get(key, 0) or 0):.2f}"
            for key in ("reach", "risk", "input", "desc", "harness")
        )
        next_read = path.get("next_read") or {}
        nr_path = _clean_inline(next_read.get("path", ""))
        nr_offset = int(next_read.get("offset", 0) or 0)
        nr_limit = int(next_read.get("limit", 0) or 0)
        lines = [
            f"{index}. score={score:.2f}{partial}{active_text} `{chain_text}`",
            f"   endpoint: `{_clean_inline(endpoint.get('function', ''), 120)}` @{file}:{line} [{role}/{family}; {kind}: {expr}]",
            f"   evidence: {evidence} [source: analysis service]",
        ]
        if nr_path:
            lines.append(f"   next: READ(path=\"{nr_path}\", offset={nr_offset}, limit={nr_limit})")
        gaps = list(path.get("gaps") or [])
        if gaps:
            reason = _clean_inline(gaps[0].get("reason", ""), 140)
            lines.append(f"   gap: {reason} [source: analysis service]")
        return "\n".join(lines)

    @staticmethod
    def render_input_mapping(mapping: Dict[str, Any]) -> str:
        status = _clean_inline(mapping.get("status", "unresolved"))
        arg = _clean_inline(mapping.get("sink_argument", "argument"))
        source = _clean_inline(mapping.get("source_parameter", "input"))
        offset = mapping.get("offset")
        offset_expr = _clean_inline(mapping.get("offset_expression", ""))
        width = mapping.get("width")
        endian = _clean_inline(mapping.get("endianness", "unknown")) or "unknown"
        transform = _clean_inline(mapping.get("transform", ""))
        constraint = _clean_inline(mapping.get("constraint", ""))
        if offset is not None and width:
            try:
                start = int(offset)
                end = start + int(width)
                region = f"{source}[0x{start:X}:0x{end:X})"
            except Exception:
                region = f"{source}[{offset_expr or '?'}:{offset_expr or '?'}+{width})"
        elif offset_expr:
            region = f"{source}[{offset_expr}:?)"
        else:
            region = f"{source}[?]"
        width_text = str(width) if width is not None else "unknown"
        line = f"[{status}] {arg} <- {region}"
        details = f"  width={width_text}; endian={endian}"
        if transform:
            details += f"; transform={transform}"
        if constraint:
            details += f"; constraint: {constraint}"
        evidence = list(mapping.get("evidence") or [])
        if evidence:
            locs = []
            for loc in evidence[:2]:
                if isinstance(loc, dict) and loc.get("file"):
                    locs.append(f"{loc.get('file')}:{loc.get('start_line') or loc.get('line') or 0}")
            if locs:
                details += f"; evidence: {' -> '.join(locs)}"
        gaps = list(mapping.get("gaps") or [])
        if gaps:
            details += f"; gap: {_clean_inline(gaps[0].get('reason', ''), 120)}"
        return line + "\n" + details + " [source: analysis service]"

    # ------------------------------------------------------------------
    # 6. Trigger condition rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_trigger(trigger: Dict[str, Any]) -> str:
        """Render a trigger condition (subset of requirements with role=trigger/hazard)."""
        # Triggers use the same shape as requirements
        role = trigger.get("role", "trigger")
        expression = str(trigger.get("expression") or "").strip()
        reason = str(trigger.get("reason") or "").strip()
        safe = str(trigger.get("safe_formula") or "").strip()
        violation = str(trigger.get("violation_formula") or "").strip()

        parts: List[str] = [f"[{role}]"]
        if expression:
            parts.append(f"  condition: {expression}")
        if reason:
            parts.append(f"  why: {reason}")
        if safe:
            parts.append(f"  safe: {safe}")
        if violation:
            parts.append(f"  trigger: {violation}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 7. Provenance rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_provenance(prov: Dict[str, Any]) -> str:
        """Render an argument provenance / dataflow trace.

        Input shape:
        {
            "sink_argument": str,
            "expression": str,
            "status": "resolved" | "partially_resolved" | "unresolved",
            "trace": list[dict],
            "origin": str,
        }
        """
        arg = str(prov.get("sink_argument") or "").strip()
        expression = str(prov.get("expression") or "").strip()
        status = str(prov.get("status") or "").strip()
        origin = str(prov.get("origin") or "").strip()

        parts: List[str] = []
        if arg:
            parts.append(f"Argument: {arg}")
        if status:
            parts.append(f"  status: {status}")
        if origin:
            parts.append(f"  origin: {origin}")
        if expression:
            parts.append(f"  expression: {expression}")

        return "\n".join(parts) if parts else "Provenance: (unspecified)"

    # ------------------------------------------------------------------
    # 8. Full brief rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_brief_sections(brief_data: Dict[str, Any]) -> Dict[str, str]:
        """Render all sections of a SinkAnalysisBrief into named Markdown strings.

        Returns a dict mapping section names to rendered Markdown strings.
        Sections with no content are omitted (empty string).

        This is the primary entry point for the observation renderer:
        it consumes the brief data and produces section content that
        can be injected into the 6-section observation structure.
        """
        sections: Dict[str, str] = {}

        # Target
        target = brief_data.get("target_resolution") or brief_data.get("target") or {}
        if target:
            rendered = IRRenderer.render_target(target)
            if rendered:
                sections["target"] = rendered

        # Paths
        paths = brief_data.get("candidate_paths") or []
        if paths:
            rendered_paths = []
            for p in paths:
                r = IRRenderer.render_path(p)
                if r:
                    rendered_paths.append(r)
            if rendered_paths:
                sections["paths"] = "\n\n".join(rendered_paths)

        # Requirements
        requirements = brief_data.get("requirements") or []
        if requirements:
            rendered_reqs = []
            for r in requirements:
                rendered = IRRenderer.render_requirement(r)
                if rendered:
                    rendered_reqs.append(rendered)
            if rendered_reqs:
                sections["requirements"] = "\n\n".join(rendered_reqs)

        # Triggers
        triggers = brief_data.get("trigger_conditions") or []
        if triggers:
            rendered_triggers = []
            for t in triggers:
                rendered = IRRenderer.render_trigger(t)
                if rendered:
                    rendered_triggers.append(rendered)
            if rendered_triggers:
                sections["triggers"] = "\n\n".join(rendered_triggers)

        # Provenance
        provenance = brief_data.get("argument_provenance") or []
        if provenance:
            rendered_prov = []
            for p in provenance:
                rendered = IRRenderer.render_provenance(p)
                if rendered:
                    rendered_prov.append(rendered)
            if rendered_prov:
                sections["provenance"] = "\n\n".join(rendered_prov)

        # Gaps
        gaps = brief_data.get("gaps") or []
        if gaps:
            rendered_gaps = []
            for g in gaps:
                rendered = IRRenderer.render_gap(g)
                if rendered:
                    rendered_gaps.append(rendered)
            if rendered_gaps:
                sections["gaps"] = "\n\n".join(rendered_gaps)

        # Alternatives — lightweight rendering
        alternatives = brief_data.get("alternatives") or []
        if alternatives:
            alt_parts = []
            for a in alternatives:
                alt_parts.append(_render_fallback_dict(a))
            if alt_parts:
                sections["alternatives"] = "\n".join(alt_parts)

        # Suggested queries — lightweight rendering
        suggested = brief_data.get("suggested_queries") or []
        if suggested:
            sq_parts = []
            for s in suggested:
                sq_parts.append(_render_fallback_dict(s))
            if sq_parts:
                sections["suggested_queries"] = "\n".join(sq_parts)

        return sections

    # ------------------------------------------------------------------
    # 9. Backward-compatible XML rendering (for transition period)
    # ------------------------------------------------------------------

    @staticmethod
    def render_brief_xml(brief_data: Dict[str, Any], brief_id: str = "",
                         candidate_id: str = "", status: str = "") -> str:
        """Render a SinkAnalysisBrief as structured XML (backward-compat).

        This replaces the html.escape(str(dict)) rendering with
        IRRenderer-based human-readable output inside the same XML
        wrapper.  This allows a gradual transition: the XML structure
        remains for static_analysis_runtime.py to inject, but the
        content is now readable.
        """
        import html as _html

        lines = [
            f'<static_analysis_result type="poc_recipe"'
            f' brief_id="{_html.escape(brief_id)}"'
            f' candidate_id="{_html.escape(candidate_id)}"'
            f' status="{_html.escape(status)}">',
        ]

        # Target
        target = brief_data.get("target_resolution") or brief_data.get("target") or {}
        lines.append("<target>")
        if target:
            lines.append(IRRenderer.render_target(target))
        lines.append("</target>")

        # Paths
        lines.append("<paths>")
        for p in (brief_data.get("candidate_paths") or []):
            lines.append(IRRenderer.render_path(p))
        lines.append("</paths>")

        # Requirements
        lines.append("<requirements>")
        for r in (brief_data.get("requirements") or []):
            lines.append(IRRenderer.render_requirement(r))
        lines.append("</requirements>")

        # Triggers
        lines.append("<triggers>")
        for t in (brief_data.get("trigger_conditions") or []):
            lines.append(IRRenderer.render_trigger(t))
        lines.append("</triggers>")

        # Provenance
        lines.append("<provenance>")
        for p in (brief_data.get("argument_provenance") or []):
            lines.append(IRRenderer.render_provenance(p))
        lines.append("</provenance>")

        # Gaps
        lines.append("<gaps>")
        for g in (brief_data.get("gaps") or []):
            lines.append(IRRenderer.render_gap(g))
        lines.append("</gaps>")

        # Alternatives
        lines.append("<alternatives>")
        for a in (brief_data.get("alternatives") or []):
            lines.append(_render_fallback_dict(a))
        lines.append("</alternatives>")

        # Suggested queries
        lines.append("<suggested_queries>")
        for s in (brief_data.get("suggested_queries") or []):
            lines.append(_render_fallback_dict(s))
        lines.append("</suggested_queries>")

        lines.append("</static_analysis_result>")
        return "\n".join(lines)


# ======================================================================
# Helper functions (module-private)
# ======================================================================

def _short_symbol(symbol_id: str) -> str:
    """Extract a short human-readable name from a symbol ID.

    Symbol IDs look like:
      'deps/pcre/pcre_jit_compile.c::compile_backtrackingpath/0@36e9d246'
      'src/fuzzer/mode_padding.cpp::ref_oneandzero_unpad/0@abc123'

    We want: 'compile_backtrackingpath' or 'ref_oneandzero_unpad'
    """
    if not symbol_id:
        return "???"
    # Split on '::' — the function name is after it
    parts = symbol_id.split("::")
    if len(parts) >= 2:
        func_part = parts[-1]
        # Remove /0@hash suffix
        if "/" in func_part:
            func_part = func_part.split("/")[0]
        if "@" in func_part:
            func_part = func_part.split("@")[0]
        return func_part
    # Fallback: just the last segment
    return symbol_id.split("/")[-1].split("@")[0]


def _clean_inline(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_origin(origin: Dict[str, Any]) -> str:
    """Format an origin dict as a compact source location string."""
    if not origin:
        return ""
    file = str(origin.get("file") or "").strip()
    line = origin.get("start_line") or origin.get("line") or ""
    if file and line:
        return f"{file}:{line}"
    if file:
        return file
    return ""


def _render_fallback_dict(d: Dict[str, Any]) -> str:
    """Fallback dict renderer — clean key=value pairs instead of str(dict).

    For dicts that don't have a dedicated renderer, produce a readable
    key=value format instead of Python repr or HTML-escaped repr.
    """
    if not d:
        return ""
    parts = []
    for k, v in d.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, (list, dict)):
            # For nested structures, keep it short
            v_str = str(v)
            if len(v_str) > 120:
                v_str = v_str[:117] + "..."
        else:
            v_str = str(v)
        parts.append(f"{k}={v_str}")
    return " | ".join(parts)
