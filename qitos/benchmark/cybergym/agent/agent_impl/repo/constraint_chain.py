"""Constraint chain and read analysis -- extracted from agent.py."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...state import CyberGymState
from ..core.fact_extraction import (
    append_capped_fact,
    best_fact_snippet,
    extract_structured_facts_from_content,
)


# ---------------------------------------------------------------------------
# _update_read_coverage  (was instance method -- self.READ_TOOL)
# ---------------------------------------------------------------------------

def update_read_coverage(agent: Any, state: CyberGymState, short_name: str, output: Any) -> None:
    """Track which file/line ranges have been READ to avoid re-reading."""
    normalized_name = str(short_name or "").upper()
    if normalized_name != agent.READ_TOOL or not isinstance(output, dict):
        return
    path = str(output.get("path") or "").strip()
    if not path:
        return
    content = str(output.get("content", "") or "")
    offset = int(output.get("offset", 0) or 0)
    lines_read = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if lines_read <= 0:
        return
    span = (offset + 1, offset + lines_read)
    if path not in state.read_coverage:
        state.read_coverage[path] = []
    state.read_coverage[path].append(span)
    # Keep only last 8 spans per file to bound growth
    if len(state.read_coverage[path]) > 8:
        state.read_coverage[path] = state.read_coverage[path][-8:]


# ---------------------------------------------------------------------------
# _confirm_constraints_from_read  (was @staticmethod)
# ---------------------------------------------------------------------------

def confirm_constraints_from_read(state: CyberGymState, output: Any) -> None:
    """P26: Promote hypothesized/unknown constraints to 'confirmed' when
    the agent READs code at a constraint's source_location and the content
    contains control-flow guard keywords (if/switch/assert/memcmp/strcmp).

    Also confirms matching ChainGate entries."""
    if not isinstance(output, dict):
        return
    read_path = str(output.get("path") or "").strip()
    content = str(output.get("content") or "")
    if not read_path or not content:
        return
    # Check for control-flow guard keywords that indicate a real condition
    guard_keywords = (
        "if", "switch", "case", "assert", "memcmp", "strcmp",
        "strncmp", "strncmp", "return", "continue", "break",
    )
    has_guard = any(kw in content for kw in guard_keywords)
    if not has_guard:
        return
    # Normalize the read path for matching
    display_path = read_path
    for constraint in list(getattr(state, "path_constraints", []) or []):
        status = str(getattr(constraint, "status", "") or "").strip().lower()
        if status == "confirmed":
            continue
        source_loc = str(getattr(constraint, "source_location", "") or "").strip()
        if not source_loc:
            continue
        # Match: the constraint's source_location should be a suffix or
        # substring of the read path (handles relative vs absolute paths)
        if source_loc in display_path or display_path.endswith(source_loc):
            constraint.status = "confirmed"
    # Also confirm matching ChainGate entries
    for gate in state.call_chain_gates:
        if gate.status == "confirmed":
            continue
        # Match gate evidence or description containing the read path
        gate_source = gate.evidence or gate.description
        if display_path in gate_source or any(
            seg in display_path for seg in gate_source.replace("/", " ").replace("\\", " ").split()
            if len(seg) > 4
        ):
            gate.status = "confirmed"
            gate.evidence = f"Confirmed by READ {read_path}"


# ---------------------------------------------------------------------------
# _constraint_source_from_read  (was @staticmethod)
# ---------------------------------------------------------------------------

def constraint_source_from_read(output: Dict[str, Any]) -> tuple[str, int]:
    """Recover parseable source and its zero-based line offset from READ output.

    READ content is decorated with a display header and ``cat -n`` style
    line numbers.  Feeding that representation to Tree-sitter changes the
    grammar, so constraint extraction strips only the known decoration.
    """
    content = str(output.get("content") or "")
    line_offset = max(0, int(output.get("offset") or 0))
    lines = content.splitlines()
    if not lines or not lines[0].startswith("// Lines "):
        return content, line_offset
    source_lines: List[str] = []
    for line in lines[1:]:
        match = re.match(r"^\s*\d+\t(.*)$", line)
        source_lines.append(match.group(1) if match else line)
    return "\n".join(source_lines), line_offset


# ---------------------------------------------------------------------------
# _extract_path_constraints_from_read  (was @staticmethod)
# ---------------------------------------------------------------------------

def extract_path_constraints_from_read(state: CyberGymState, output: Any) -> None:
    """Auto-extract constraints from READ content using tree-sitter AST.

    Two-tier design:
      - High-confidence constraints (format_gate from memcmp/strcmp)
        -> directly create ChainGate (status="inferred").
      - Medium/low-confidence constraints (bounds/dispatch/path)
        -> stored as suggested_constraints for LLM judgment.

    Uses tree-sitter for AST-level extraction when available,
    falling back to regex Pattern 1 (format_gate) otherwise.
    """
    if not isinstance(output, dict):
        return
    read_path = str(output.get("path") or "").strip()
    content, source_line_offset = constraint_source_from_read(output)
    if not read_path or not content:
        return
    existing_descriptions = {
        str(getattr(c, "description", "") or "").strip()
        for c in list(getattr(state, "path_constraints", []) or [])
    }
    from ...state import ChainGate, PathConstraint

    # Resolve explicit caller -> next-hop edges from the ordered chain.
    # A file may contain several chain nodes, so extract each edge
    # independently and retain each target callsite span.
    ordered_nodes = sorted(state.call_chain_nodes, key=lambda item: item.order)
    edge_specs = []
    for index, node in enumerate(ordered_nodes[:-1]):
        node_path = node.location.split(":")[0]
        if node_path and (read_path.endswith(node_path) or node_path in read_path):
            edge_specs.append((node, ordered_nodes[index + 1]))

    # Existing suggestion/gate descriptions for dedup
    existing_suggestion_descs = {
        s.get("description", "") for s in state.suggested_constraints
    }
    existing_gate_descs = {g.description for g in state.call_chain_gates}
    # --- Level-1 source-only tree-sitter analysis ---
    from ...analysis.constraints.analysis import analyze_constraint_requests
    from ...analysis.constraints.models import ExtractionRequest, SourceUnit, hint_from_description

    # Determine file extension for C vs C++ parser selection
    ext = ".c"
    if read_path.endswith((".cpp", ".cc", ".cxx", ".hpp")):
        ext = ".cpp"
    elif read_path.endswith(".h"):
        # The extractor tries both grammars for ambiguous headers.
        ext = ".h"

    candidates = []
    analysis_paths: List[Dict[str, Any]] = []
    analysis_diagnostics: List[Dict[str, Any]] = []
    hint = hint_from_description(state.vulnerability_description)
    state.vulnerability_hints = list(hint.families)
    is_partial = bool(
        int(output.get("offset") or 0) > 0
        or output.get("has_more")
        or output.get("truncated")
    )
    has_complete_function = bool(
        re.search(r"\b[A-Za-z_~][\w:~]*\s*\([^;{}]*\)\s*\{", content)
        and content.count("{") == content.count("}")
    )
    source_unit = SourceUnit(
        text=content,
        path=read_path,
        file_extension=ext,
        line_offset=source_line_offset,
        completeness=(
            "full_function" if is_partial and has_complete_function
            else "snippet" if is_partial
            else "full_file"
        ),
    )

    def collect_result(result) -> None:
        candidates.extend(result.candidates)
        analysis_paths.extend({
            "path_id": path.path_id,
            "target_function": path.target_function,
            "required_formula": path.required_formula,
            "anchor_span": path.anchor_span.as_dict(),
        } for path in result.paths)
        analysis_diagnostics.extend({
            "code": item.code,
            "message": item.message,
            "severity": item.severity,
            "source_span": item.source_span.as_dict() if item.source_span else {},
            "source": read_path,
        } for item in result.diagnostics)

    analysis_requests = []
    if edge_specs:
        for caller_node, target_node in edge_specs:
            analysis_requests.append(ExtractionRequest(
                source=source_unit,
                caller_function=caller_node.function,
                target_function=target_node.function,
                vulnerability_hint=hint,
            ))
    else:
        # Legacy compatibility is intentionally limited to one unambiguous
        # target and is downgraded by the extractor.
        matching_nodes = [
            node for node in ordered_nodes
            if (node_path := node.location.split(":")[0])
            and (read_path.endswith(node_path) or node_path in read_path)
        ]
        known_funcs = set(state.vulnerable_functions or [])
        if matching_nodes and len(known_funcs) == 1:
            analysis_requests.append(ExtractionRequest(
                source=source_unit,
                caller_function=matching_nodes[0].function,
                target_function=next(iter(known_funcs)),
                vulnerability_hint=hint,
            ))

    # The final chain node is the vulnerability-relative sink.  Trigger
    # detectors are deliberately run only when its source is READ.
    if ordered_nodes:
        sink_node = ordered_nodes[-1]
        sink_path = sink_node.location.split(":")[0]
        if sink_path and (read_path.endswith(sink_path) or sink_path in read_path):
            analysis_requests.append(ExtractionRequest(
                source=source_unit,
                sink_function=sink_node.function,
                vulnerability_hint=hint,
            ))

    for analysis_result in analyze_constraint_requests(analysis_requests):
        collect_result(analysis_result)

    # Adjacent chain edges can overlap in malformed/incomplete chain state.
    unique_candidates = {}
    for candidate in candidates:
        span = candidate.target_call_span
        key = (
            candidate.node_function,
            candidate.target_function,
            span.start_byte if span else -1,
            span.end_byte if span else -1,
            candidate.origin,
            candidate.normalized_formula,
            candidate.role,
            candidate.path_id,
        )
        unique_candidates[key] = candidate
    candidates = list(unique_candidates.values())

    new_gates: List[ChainGate] = []
    new_suggestions: List[Dict[str, Any]] = []

    for cand in candidates:
        desc = cand.description
        if desc in existing_gate_descs or desc in existing_descriptions:
            continue
        # Legacy path_constraints for backward compat
        if desc not in existing_descriptions:
            state.path_constraints.append(
                PathConstraint(
                    description=desc,
                    source_location=read_path,
                    status="hypothesized",
                    required_values=cand.normalized_formula,
                    constraint_type=cand.gate_type,
                )
            )
            existing_descriptions.add(desc)

        if cand.promotable and cand.role == "reachability" and cand.confidence == "high":
            # Only source-proven reachability constraints may auto-promote.
            brief = f"{cand.gate_type} at {read_path}: {cand.required_condition[:80]}"
            state.gate_evidence_brief[desc] = brief
            node_order = next(
                (node.order for node in ordered_nodes if node.function == cand.node_function),
                0,
            )
            target_line = cand.target_call_span.start_line if cand.target_call_span else 0
            new_gates.append(ChainGate(
                node_order=node_order,
                gate_type=cand.gate_type,
                description=desc,
                required_condition=cand.required_condition,
                status="inferred",
                evidence=f"READ {read_path}:{target_line}" if target_line else f"READ {read_path}",
                repair_hint="",
                role=cand.role,
                path_id=cand.path_id,
                source_span=cand.source_span.as_dict() if cand.source_span else {},
            ))
        elif desc not in existing_suggestion_descs:
            # Trigger/hazard/dataflow candidates always require model review.
            # Low-confidence semantic hazards remain visible as warnings,
            # but cannot become gates without an explicit record_gate call.
            if cand.polarity == "satisfy":
                new_suggestions.append({
                    "gate_type": cand.gate_type,
                    "description": desc,
                    "required_condition": cand.required_condition,
                    "normalized_formula": cand.normalized_formula,
                    "raw_condition": cand.raw_condition,
                    "source": read_path,
                    "polarity": cand.polarity,
                    "origin": cand.origin,
                    "node_function": cand.node_function,
                    "target_function": cand.target_function,
                    "source_span": cand.source_span.as_dict() if cand.source_span else {},
                    "target_call_span": cand.target_call_span.as_dict() if cand.target_call_span else {},
                    "sink_span": cand.sink_span.as_dict() if cand.sink_span else {},
                    "access_mode": cand.access_mode,
                    "role": cand.role,
                    "path_id": cand.path_id,
                    "confidence": cand.confidence,
                    "safe_formula": cand.safe_formula,
                    "violation_formula": cand.violation_formula,
                    "promotable": cand.promotable,
                    "confidence_reasons": list(cand.confidence_reasons),
                    "symbol_dependencies": list(cand.symbol_dependencies),
                    "semantic_tags": list(cand.semantic_tags),
                })
                existing_suggestion_descs.add(desc)

    # Keep suggestions diverse across callsite path, role, and gate type.
    combined = [*state.suggested_constraints, *new_suggestions]
    deduplicated: Dict[tuple, Dict[str, Any]] = {}
    for suggestion in reversed(combined):
        key = (
            suggestion.get("path_id", ""),
            suggestion.get("role", "reachability"),
            suggestion.get("gate_type", "path_gate"),
            suggestion.get("normalized_formula", ""),
        )
        deduplicated.setdefault(key, suggestion)
    ordered = list(reversed(list(deduplicated.values())))
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for suggestion in ordered:
        group_key = (
            suggestion.get("path_id", ""),
            suggestion.get("role", "reachability"),
            suggestion.get("gate_type", "path_gate"),
        )
        groups.setdefault(group_key, []).append(suggestion)
    selected: List[Dict[str, Any]] = []
    while len(selected) < 24 and any(groups.values()):
        for group in groups.values():
            if group and len(selected) < 24:
                selected.append(group.pop(0))
    state.suggested_constraints = selected
    if analysis_paths:
        path_index = {item.get("path_id", ""): item for item in state.constraint_paths}
        for item in analysis_paths:
            path_index[item["path_id"]] = item
        state.constraint_paths = list(path_index.values())[-32:]
    if analysis_diagnostics:
        state.constraint_diagnostics.extend(analysis_diagnostics)
        state.constraint_diagnostics = state.constraint_diagnostics[-32:]

    # Add deduplicated ChainGate entries
    existing_gate_descs = {g.description for g in state.call_chain_gates}
    added_any = False
    for gate in new_gates:
        if gate.description not in existing_gate_descs:
            state.call_chain_gates.append(gate)
            existing_gate_descs.add(gate.description)
            added_any = True
    if new_gates or new_suggestions:
        state.gate_board_last_changed_step = getattr(state, "current_step", 0) or 0
    # Cap total constraints to prevent unbounded growth
    if len(state.path_constraints) > 30:
        state.path_constraints = state.path_constraints[-30:]
    if len(state.call_chain_gates) > 40:
        state.call_chain_gates = state.call_chain_gates[-40:]


# ---------------------------------------------------------------------------
# _check_and_flag_contradictions  (was @staticmethod)
# ---------------------------------------------------------------------------

def check_and_flag_contradictions(state: CyberGymState) -> None:
    """Detect contradictions between chain gates and downgrade the latest
    confirmed gate to 'questioned' if a contradiction is found."""
    from ...agent_impl.observation.memory import _detect_gate_contradictions
    contradictions = _detect_gate_contradictions(
        state.call_chain_gates,
        suggestions=list(getattr(state, "suggested_constraints", []) or []),
    )
    if contradictions:
        # Downgrade the latest confirmed gate to questioned
        confirmed = [g for g in state.call_chain_gates if g.status == "confirmed"]
        if confirmed:
            latest = max(confirmed, key=lambda g: state.call_chain_gates.index(g))
            latest.status = "questioned"
            latest.evidence = f"Downgraded due to contradiction: {contradictions[0]}"


# ---------------------------------------------------------------------------
# _infer_chain_from_search  (was @staticmethod)
# ---------------------------------------------------------------------------

def infer_chain_from_search(
    state: CyberGymState,
    short_name: str,
    output: Any,
) -> None:
    """Auto-infer ChainNode entries from FindSymbols/CallsiteSearch results.

    The LLM's search query is the signal -- if it searched for a function,
    it believes that function matters.  Only top-scoring function/definition
    hits are promoted to chain nodes with status="inferred".  The LLM must
    still confirm by reading the code.
    """
    from ...state import ChainNode

    if not isinstance(output, dict):
        return

    # Extract the relevant result list based on tool type
    if short_name in ("FindSymbols", "find_symbols"):
        results = output.get("results") or []
        eligible = [
            r for r in results[:5]
            if str(r.get("kind", "") or "").lower() in ("function", "definition")
        ]
    elif short_name in ("CallsiteSearch", "callsite_search"):
        # definitions field contains function definition hits
        eligible = (output.get("definitions") or [])[:5]
    else:
        return

    if not eligible:
        return

    existing_keys = {
        f"{n.function}@{n.location}@{n.sink_id}" for n in state.call_chain_nodes
    }
    vulnerable = set(state.vulnerable_functions or [])
    # Build a map from function name to sink_id for matching SinkCandidates
    sink_func_to_id: Dict[str, str] = {}
    for c in (state.sink_candidates or []):
        if c.status != "eliminated":
            sink_func_to_id[c.function] = f"{c.function}@{c.location}"
    primary_sink_id = state._primary_sink_id()
    _SKIP_NAMES = {"if", "while", "for", "switch", "return", "sizeof", "main"}

    for item in eligible:
        func = str(
            item.get("name") or item.get("symbol") or ""
        ).strip()
        if not func or func in _SKIP_NAMES:
            continue

        path = str(item.get("path") or "").strip()
        line_no = int(item.get("line_number") or 0)
        location = f"{path}:{line_no}" if line_no else path
        if not location:
            continue

        # Determine sink_id for this node
        node_sink_id = ""
        # Infer role from position and function name
        if not state.call_chain_nodes:
            role = "entry"
        elif func in vulnerable or func in sink_func_to_id:
            role = "sink"
            # Assign sink_id from matching SinkCandidate
            if func in sink_func_to_id:
                node_sink_id = sink_func_to_id[func]
                # Upgrade matching SinkCandidate to confirmed
                for c in state.sink_candidates:
                    if c.function == func and c.status == "candidate":
                        c.status = "confirmed"
                        break
            elif primary_sink_id:
                node_sink_id = primary_sink_id
        elif any(
            kw in func.lower()
            for kw in ("parse", "read", "decode", "process")
        ):
            role = "parser"
        elif any(
            kw in func.lower()
            for kw in ("dispatch", "route", "handle", "select")
        ):
            role = "dispatch"
        else:
            role = "parser"

        # For non-sink roles, inherit sink_id from last sink node or primary
        if not node_sink_id and state.call_chain_nodes:
            # Check if any recent node has a sink_id
            for n in reversed(state.call_chain_nodes):
                if n.sink_id:
                    node_sink_id = n.sink_id
                    break
        if not node_sink_id:
            node_sink_id = primary_sink_id

        key = f"{func}@{location}@{node_sink_id}"
        if key in existing_keys:
            continue

        # Assign order within same sink_id
        same_sink_orders = [
            n.order for n in state.call_chain_nodes
            if n.sink_id == node_sink_id
            or (not n.sink_id and node_sink_id == primary_sink_id)
        ]
        max_order = max(same_sink_orders, default=-1)

        state.call_chain_nodes.append(ChainNode(
            location=location,
            function=func,
            role=role,
            description=f"Found via {short_name} (inferred)",
            status="inferred",
            evidence=f"{short_name} query result",
            order=max_order + 1,
            sink_id=node_sink_id,
        ))
        existing_keys.add(key)

    # Cap at 20 nodes
    if len(state.call_chain_nodes) > 20:
        state.call_chain_nodes = state.call_chain_nodes[-20:]


# ---------------------------------------------------------------------------
# _refute_gate  (was @staticmethod)
# ---------------------------------------------------------------------------

def refute_gate(state: CyberGymState, gate_index: int, evidence: str, repair_hint: str) -> None:
    """Mark a ChainGate as refuted with evidence and a repair hint.

    Refuted gates are never deleted -- they carry learning that prevents
    the agent from retrying the same approach.
    """
    if 0 <= gate_index < len(state.call_chain_gates):
        gate = state.call_chain_gates[gate_index]
        gate.status = "refuted"
        gate.evidence = evidence
        gate.repair_hint = repair_hint


# ---------------------------------------------------------------------------
# _update_chain_from_read  (was @staticmethod)
# ---------------------------------------------------------------------------

def update_chain_from_read(state: CyberGymState, output: Any) -> None:
    """Insert or update ChainNode entries from READ content.

    Detects function calls and control-flow structures that form the
    entry-to-sink call chain.
    """
    if not isinstance(output, dict):
        return
    read_path = str(output.get("path") or "").strip()
    content = str(output.get("content") or "")
    if not read_path or not content:
        return

    from ...state import ChainNode

    existing_locs = {n.location for n in state.call_chain_nodes}
    max_order = max((n.order for n in state.call_chain_nodes), default=-1)

    # Detect function definitions -- these are chain nodes
    for m in re.finditer(
        r'(?:(?:static\s+)?(?:inline\s+)?[\w:*&]+\s+)(\w+)\s*\([^)]*\)\s*(?:\{|$)',
        content,
    ):
        func_name = m.group(1)
        # Skip trivial C keywords
        if func_name in ("if", "while", "for", "switch", "return", "sizeof", "typedef"):
            continue
        loc = f"{read_path}"
        # Only add if not already present at this location
        key = f"{func_name}@{loc}"
        if key not in existing_locs:
            # Determine role based on position and context
            role = "parser"
            if max_order < 0:
                role = "entry"
            elif func_name in (state.vulnerable_functions or []):
                role = "sink"
            state.call_chain_nodes.append(ChainNode(
                location=loc,
                function=func_name,
                role=role,
                description=f"Function {func_name} in {read_path}",
                status="inferred",
                evidence=f"READ {read_path}",
                order=max_order + 1,
            ))
            existing_locs.add(key)
            max_order += 1

    # Cap chain nodes
    if len(state.call_chain_nodes) > 20:
        state.call_chain_nodes = state.call_chain_nodes[-20:]


# ---------------------------------------------------------------------------
# _capture_read_fact  (was instance method -- self.READ_TOOL, self._*)
# ---------------------------------------------------------------------------

def capture_read_fact(agent: Any, state: CyberGymState, short_name: str, output: Any) -> None:
    normalized_name = str(short_name or "").upper()
    if normalized_name != agent.READ_TOOL or not isinstance(output, dict):
        return
    path = str(output.get("path") or "").strip()
    content = str(output.get("content") or "")
    if not path or not content.strip():
        return
    snippet = best_fact_snippet(content)
    if not snippet:
        return
    evidence = state.durable_project_memory or {}
    parser_paths = set(evidence.get("parser_paths") or [])
    field_paths = set(evidence.get("field_paths") or [])
    seed_paths = set(evidence.get("seed_paths") or [])
    if path in parser_paths:
        prefix = "parser_path"
    elif path in field_paths:
        prefix = "field_path"
    elif path in seed_paths:
        prefix = "seed_path"
    else:
        prefix = "read_fact"
    fact = f"{prefix}: {agent._display_path(path, state=state)} -> {snippet}"
    state.durable_code_facts = append_capped_fact(state.durable_code_facts, fact)
    # Extract structured facts from parser/field/seed paths
    if prefix in ("parser_path", "field_path"):
        structured = extract_structured_facts_from_content(content, agent._display_path(path, state=state))
        for sfact in structured:
            state.durable_code_facts = append_capped_fact(state.durable_code_facts, sfact)


# ---------------------------------------------------------------------------
# _display_path  (was instance method -- self.workspace_root fallback)
# ---------------------------------------------------------------------------

def display_path(path: str, *, state: Optional[CyberGymState] = None, agent: Any = None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    workspace_root = str(
        (state.workspace_root if state is not None else "") or getattr(agent, "workspace_root", "") or ""
    ).strip()
    if not workspace_root:
        return raw
    try:
        raw_path = Path(raw)
        if not raw_path.is_absolute():
            return raw
        root = Path(workspace_root).resolve()
        resolved = raw_path.resolve()
        return str(resolved.relative_to(root))
    except Exception:
        return raw
