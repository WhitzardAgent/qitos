"""Path ranking and vulnerability path discovery extracted from AnalysisService."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import (
    CallEdge, FunctionSummary, FunctionSymbol, RankedVulnerabilityPath,
    RiskSignal, SinkAnalysisBrief, SinkCandidateInput, SourceLocation,
    stable_value,
)
from .navigation_service import _description_values, _estimate_tokens
from .store import ANALYSIS_VERSION, GRAMMAR_VERSION, _expr

_LOG = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level static helpers
# ---------------------------------------------------------------------------

def _target_resolution(
    candidate: SinkCandidateInput,
    target: FunctionSymbol,
    method: str,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "status": "confirmed" if method in {"enclosing_function", "function_symbol"} else "inferred",
        "method": method,
        "requested": {
            "function": candidate.function, "callee": candidate.callee,
            "expression": candidate.expression, "file": candidate.file, "line": candidate.line,
        },
        "symbol_id": target.symbol_id,
        "function": target.qualified_name,
        "file": target.file,
        "location": stable_value(target.body_location),
        "evidence": evidence,
    }


def _requirement_from_constraint(item: dict[str, Any], path_id: str, order: int) -> dict[str, Any]:
    expression = str(item.get("normalized_text") or item.get("expression") or "").strip()
    origin = item.get("origin_location") or item.get("origin") or {}
    material = json.dumps([path_id, order, expression, origin], sort_keys=True, default=str)
    return {
        "requirement_id": "req_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest(),
        "path_id": path_id,
        "order": order,
        "role": item.get("role", "reachability"),
        "gate_type": item.get("gate_type", "path_gate"),
        "expression": expression,
        "safe_formula": item.get("safe_formula", ""),
        "violation_formula": item.get("violation_formula", ""),
        "input_mapping": item.get("input_mapping", ""),
        "status": item.get("status", "inferred"),
        "confidence": float(item.get("confidence", item.get("confidence_score", .5)) or .5),
        "origin": origin,
        "reason": item.get("reason") or item.get("description") or "source control dependence",
    }


def _normalize_ranked_path_chain(
    entry_ids: set[str],
    endpoint_id: str,
    chain: list[str],
    status: str,
) -> tuple[list[str], str, list[dict[str, Any]], bool]:
    """Return an entry->endpoint, acyclic chain plus explicit warnings.

    Ranked paths are consumed directly by the LLM. If a fallback path is
    reversed or contains duplicate nodes, silently passing it through makes
    the prompt look more certain than the static evidence really is. This
    helper normalizes common static-analysis artifacts and marks the path as
    partial whenever the chain had to be repaired.
    """
    warnings: list[dict[str, Any]] = []
    loop_detected = False
    normalized = [str(item) for item in (chain or []) if str(item)]

    def warn(wid: str, reason: str, **extra: Any) -> None:
        payload = {"id": wid, "reason": reason}
        payload.update(extra)
        warnings.append(payload)

    if not normalized:
        normalized = [endpoint_id]
        warn("path_empty_endpoint_only", "no chain symbols were available; using endpoint only")

    # Common bug: fallback/source stack is sink->...->entry. Recover the
    # direction when the last node is an entry and the endpoint is first.
    if normalized and normalized[0] not in entry_ids and normalized[-1] in entry_ids:
        normalized = list(reversed(normalized))
        warn("path_direction_reversed", "path was reversed to entry-to-sink order")

    # If an entry exists later in the chain, trim the invalid prefix instead
    # of presenting sink-before-entry as a real call direction.
    if normalized and normalized[0] not in entry_ids:
        entry_positions = [idx for idx, sid in enumerate(normalized) if sid in entry_ids]
        if entry_positions:
            idx = entry_positions[0]
            normalized = normalized[idx:]
            warn(
                "path_invalid_prefix_removed",
                "symbols before the harness entry were removed from the ranked path",
                removed_prefix_len=idx,
            )
        else:
            warn("path_missing_entry", "no harness entry symbol appears in the ranked path")

    # Remove consecutive duplicates first (A->B->B->C).
    compact: list[str] = []
    for sid in normalized:
        if compact and compact[-1] == sid:
            loop_detected = True
            warn("path_consecutive_duplicate_node", "removed consecutive duplicate path node", symbol_id=sid)
            continue
        compact.append(sid)
    normalized = compact

    # Remove non-consecutive duplicate nodes (A->B->C->B->D). Keeping the
    # first occurrence preserves a shortest acyclic prefix for prompt use.
    seen: set[str] = set()
    acyclic: list[str] = []
    for sid in normalized:
        if sid in seen:
            loop_detected = True
            warn("path_duplicate_node_removed", "removed repeated path node to avoid a synthetic loop", symbol_id=sid)
            continue
        seen.add(sid)
        acyclic.append(sid)
    normalized = acyclic

    if endpoint_id in normalized and normalized[-1] != endpoint_id:
        endpoint_index = normalized.index(endpoint_id)
        trailing = len(normalized) - endpoint_index - 1
        normalized = normalized[: endpoint_index + 1]
        warn(
            "path_endpoint_not_terminal",
            "symbols after the sink endpoint were removed",
            removed_suffix_len=trailing,
        )
    elif endpoint_id not in normalized:
        normalized.append(endpoint_id)
        warn("path_endpoint_appended", "endpoint was absent from the chain and was appended as a partial sink")

    if warnings:
        warning_ids = {item["id"] for item in warnings}
        if warning_ids <= {"path_empty_endpoint_only", "path_missing_entry", "path_endpoint_appended"}:
            status = "partial"
        elif "path_direction_reversed" in warning_ids and normalized and normalized[0] in entry_ids and normalized[-1] == endpoint_id:
            status = "partial_recovered_direction"
        elif any("duplicate" in wid for wid in warning_ids):
            status = "partial_duplicate_nodes"
        elif normalized and normalized[0] not in entry_ids:
            status = "partial_invalid_direction"
        else:
            status = "partial_normalized"
    return normalized, status, warnings, loop_detected


def _signal_kind(signal: RiskSignal | dict[str, Any] | None) -> str:
    if signal is None:
        return ""
    return str(getattr(signal, "kind", "") if not isinstance(signal, dict) else signal.get("kind", ""))


def _event_role_for_signal(signal: RiskSignal | dict[str, Any] | None) -> str:
    kind = _signal_kind(signal)
    text = ""
    if signal is not None:
        text = str(getattr(signal, "reason", "") if not isinstance(signal, dict) else signal.get("reason", "")).lower()
        text += " " + str(getattr(signal, "expression", "") if not isinstance(signal, dict) else signal.get("expression", "")).lower()
    tokens = set(re.findall(r"[a-z_][a-z0-9_]*", text))
    init_tokens = {"init", "initialize", "initialise", "initializer", "initialiser", "initialized", "initialised"}
    if kind in {"partial_initialization", "out_param_write"} or "origin" in tokens or bool(tokens & init_tokens):
        return "origin"
    if kind in {"branch_on_value", "uninitialized_read", "use_uninitialized", "typed_read"}:
        return "use"
    if kind in {"lifecycle", "lifecycle_invalidation", "free"} or any(token in text for token in ("free", "destroy", "release", "delete", "realloc", "unref")):
        return "invalidation"
    if kind in {"pointer_dereference", "pointer_use", "field_access", "memory_copy", "typed_write"}:
        return "use"
    if kind in {"input_arithmetic", "integer_overflow", "negative_size"}:
        return "arithmetic"
    if kind in {"allocation", "memory_copy", "array_access", "loop_progress"}:
        return "consumer"
    if kind in {"memcpy_overlap"}:
        return "src_range"
    return ""


# ---------------------------------------------------------------------------
# PathRankingService
# ---------------------------------------------------------------------------

class PathRankingService:
    """Handles target resolution, path ranking, and vulnerability path discovery."""

    def __init__(self, service: Any, navigation: Any) -> None:
        self._service = service
        self._navigation = navigation

    # -- target resolution --------------------------------------------------

    def _resolve_target(self, candidate: SinkCandidateInput) -> tuple[FunctionSymbol | None, dict[str, Any]]:
        """Resolve function/call-expression candidates without inventing uniqueness."""
        svc = self._service
        file = svc._resolve_file_name(candidate.file)
        symbols = list(svc.symbols)
        by_id = {item.symbol_id: item for item in symbols}
        evidence: list[str] = []

        if file and candidate.line:
            located = [
                item for item in symbols
                if item.file == file
                and item.body_location.start_line <= candidate.line <= item.body_location.end_line
            ]
            if len(located) == 1:
                target = located[0]
                evidence.append("file+line resolved enclosing function")
                return target, _target_resolution(candidate, target, "enclosing_function", evidence)

        query = str(candidate.function or "").strip()
        if query:
            matches = [
                item for item in symbols
                if item.symbol_id == query or item.qualified_name == query or item.name == query
            ]
            file_matches = [item for item in matches if not file or item.file == file]
            selected = file_matches or matches
            if len(selected) == 1:
                evidence.append("exact function symbol")
                return selected[0], _target_resolution(candidate, selected[0], "function_symbol", evidence)

        expressions = [str(x or "").strip() for x in (candidate.callee, candidate.expression, candidate.function) if str(x or "").strip()]
        call_matches: list[tuple[FunctionSymbol, Any]] = []
        for summary in svc.summaries:
            caller = by_id.get(summary.function_id)
            if caller is None or (file and caller.file != file):
                continue
            for call in summary.calls:
                if candidate.line and abs(call.location.start_line - candidate.line) > 2:
                    continue
                leaf = call.callee_text.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]
                if any(expr == call.callee_text or expr == leaf or expr in call.callee_text for expr in expressions):
                    call_matches.append((caller, call))
        callers = {caller.symbol_id: caller for caller, _call in call_matches}
        if len(callers) == 1:
            target = next(iter(callers.values()))
            call = call_matches[0][1]
            evidence.append(f"call expression {call.callee_text} resolved to enclosing function")
            value = _target_resolution(candidate, target, "callsite_enclosing_function", evidence)
            value["sink_expression"] = call.callee_text
            value["sink_location"] = stable_value(call.location)
            value["callsite_id"] = call.callsite_id
            return target, value

        candidates = sorted({item.symbol_id for item in (file_matches if query and 'file_matches' in locals() else [])})
        if not candidates:
            candidates = sorted(callers)[:8]
        return None, {
            "status": "unresolved", "requested": stable_value(candidate),
            "reason": "target_not_uniquely_located", "candidate_symbol_ids": candidates,
            "evidence": evidence,
        }

    # -- path helpers -------------------------------------------------------

    def _selected_entry_ids(self, harness: dict[str, Any] | None) -> tuple[set[str], list[dict[str, Any]]]:
        svc = self._service
        gaps: list[dict[str, Any]] = []
        selected_path = str((harness or {}).get("source_path") or "")
        selected_entry = str((harness or {}).get("entry_function") or "")
        selected_line = int((harness or {}).get("line") or 0)
        candidates = list(svc.entrypoints)
        if selected_entry:
            matches = [
                item.symbol_id for item in svc.symbols
                if item.name == selected_entry
                and (not selected_path or item.file == selected_path)
                and (not selected_line or abs(item.body_location.start_line - selected_line) <= 3)
            ]
            if len(matches) == 1:
                return {matches[0]}, gaps
            if matches:
                gaps.append({
                    "id": "ambiguous_selected_harness_entry",
                    "reason": f"{len(matches)} symbols match selected harness",
                    "candidate_symbol_ids": matches[:8],
                })
                return set(matches), gaps
            gaps.append({
                "id": "selected_harness_entry_not_resolved",
                "reason": f"could not resolve {selected_entry} @{selected_path}:{selected_line}",
            })
        if not candidates:
            gaps.append({"id": "entrypoint_required", "reason": "no indexed fuzz entrypoint found"})
        return set(candidates), gaps

    def _path_to_endpoint(self, entry_ids: set[str], endpoint_id: str, max_depth: int) -> tuple[list[str], list[CallEdge], str, list[dict[str, Any]]]:
        svc = self._service
        by_caller: dict[str, list[CallEdge]] = {}
        for edge in svc.edges:
            by_caller.setdefault(edge.caller_id, []).append(edge)
        parent: dict[str, tuple[str, CallEdge]] = {}
        queue = [(eid, 0) for eid in entry_ids]
        seen = set(entry_ids)
        while queue:
            current, depth = queue.pop(0)
            if current == endpoint_id:
                chain = [current]
                edges: list[CallEdge] = []
                while current in parent:
                    prev, edge = parent[current]
                    edges.append(edge)
                    current = prev
                    chain.append(current)
                return list(reversed(chain)), list(reversed(edges)), "resolved", []
            if depth >= max_depth:
                continue
            for edge in sorted(by_caller.get(current, []), key=lambda e: -e.confidence):
                if edge.callee_id in seen:
                    continue
                seen.add(edge.callee_id)
                parent[edge.callee_id] = (current, edge)
                queue.append((edge.callee_id, depth + 1))
        if endpoint_id in svc.entry_paths:
            chain = list(svc.entry_paths.get(endpoint_id) or [])
            if chain:
                return chain, [], "partial", [{
                    "id": "path_edges_not_reconstructed",
                    "reason": "entry_paths had symbols but callsite edges were incomplete",
                }]
        return [endpoint_id], [], "partial", [{
            "id": "entry_to_endpoint_path_unresolved",
            "reason": f"no path within depth {max_depth}; unresolved/indirect calls may exist",
        }]

    def _paired_endpoint_for_role(
        self,
        *,
        endpoint_id: str,
        needed_role: str,
        semantics_family: str,
        summaries: dict[str, FunctionSummary],
        by_id: dict[str, FunctionSymbol],
    ) -> dict[str, Any]:
        if not needed_role:
            return {}
        best: tuple[float, str, RiskSignal] | None = None
        for sid, summary in summaries.items():
            if sid == endpoint_id or sid not in by_id:
                continue
            for signal in list(summary.risk_signals or []):
                if _event_role_for_signal(signal) != needed_role:
                    continue
                score = float(signal.severity or 0.0)
                if semantics_family == "uninitialized" and needed_role == "origin" and signal.kind in {"partial_initialization", "out_param_write"}:
                    score += .25
                if semantics_family == "lifetime" and needed_role == "invalidation" and signal.kind in {"lifecycle", "lifecycle_invalidation"}:
                    score += .25
                if best is None or score > best[0]:
                    best = (score, sid, signal)
        if best is None:
            return {}
        _, sid, signal = best
        sym = by_id[sid]
        return {
            "symbol_id": sid,
            "function": sym.qualified_name,
            "file": sym.file,
            "line": sym.body_location.start_line,
            "event_role": needed_role,
            "signal": {
                "signal_id": signal.signal_id,
                "kind": signal.kind,
                "expression": signal.expression,
                "severity": signal.severity,
                "reason": signal.reason,
            },
        }

    # -- main ranking pipeline ----------------------------------------------

    def discover_ranked_vulnerability_paths(
        self,
        *,
        description_analysis: dict[str, Any] | None = None,
        verified_refs: list[dict[str, Any]] | None = None,
        harness: dict[str, Any] | None = None,
        crash_type: str = "",
        fast_depth: int = 8,
        deep_depth: int = 24,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Return diversified, source-backed candidate vulnerability paths."""
        svc = self._service
        svc._ensure(svc.config.automatic_timeout_seconds)
        top_k = max(1, min(int(top_k or 5), 10))
        from .vulnerability_knowledge import (
            classify_endpoint_role,
            endpoint_semantics,
            required_event_pairs,
            role_schemas_for_crash_type,
            score_endpoint_for_role,
            score_risk_signal,
        )
        from .vuln_patterns import is_entry_point_function

        semantics = endpoint_semantics(crash_type)
        role_schemas = role_schemas_for_crash_type(crash_type)
        required_pairs = required_event_pairs(crash_type)
        entry_ids, entry_gaps = self._selected_entry_ids(harness)
        verified_refs = list(verified_refs or [])
        ref_symbol_ids = {
            str(item.get("symbol_id") or "") for item in verified_refs
            if str(item.get("symbol_id") or "")
        }
        ref_files = {
            str(item.get("file") or "") for item in verified_refs
            if str(item.get("file") or "")
        }
        ref_by_symbol: dict[str, list[str]] = {}
        for item in verified_refs:
            sid = str(item.get("symbol_id") or "")
            if sid:
                ref_by_symbol.setdefault(sid, []).append(str(item.get("ref_id") or ""))
        suspect_funcs = {value.casefold() for value in _description_values(description_analysis, "suspect_functions")}
        suspect_files = {value.casefold() for value in _description_values(description_analysis, "suspect_files")}
        harness_first_hops = {
            str(item).casefold()
            for item in ((harness or {}).get("first_hops") or [])
            if str(item).strip()
        }

        rows = self._navigation._navigation_rows(
            description=" ".join(
                _description_values(description_analysis, "suspect_functions")
                + _description_values(description_analysis, "search_hints")
            ),
            focus_symbol_ids=ref_symbol_ids,
            crash_type=crash_type,
        )[:50]
        by_id = {item.symbol_id: item for item in svc.symbols}
        summaries = {item.function_id: item for item in svc.summaries}
        pool: list[RankedVulnerabilityPath] = []
        endpoint_seen: set[tuple[str, str]] = set()

        for row in rows:
            endpoint_id = str(row.get("symbol_id") or "")
            symbol = by_id.get(endpoint_id)
            if symbol is None or is_entry_point_function(symbol.name):
                continue
            summary = summaries.get(endpoint_id)
            risk_signals = list(summary.risk_signals if summary else [])
            if not risk_signals:
                fake_signal = RiskSignal(
                    signal_id=f"path_anchor_{hashlib.blake2s(endpoint_id.encode(), digest_size=5).hexdigest()}",
                    kind="path_anchor",
                    expression=symbol.name,
                    location=symbol.body_location,
                    severity=.25,
                    reason="reachable or description-focused path anchor",
                )
                risk_signals = [fake_signal]
            best_signal = max(risk_signals, key=lambda sig: score_risk_signal(sig, semantics))
            best_role_schema = max(role_schemas, key=lambda schema: score_endpoint_for_role(best_signal, schema))
            role_score = score_endpoint_for_role(best_signal, best_role_schema)
            risk_semantics = 0.30 * score_risk_signal(best_signal, semantics)

            chain, path_edges, status, gaps = self._path_to_endpoint(entry_ids, endpoint_id, fast_depth)
            if status != "resolved" and entry_ids:
                deep_chain, deep_edges, deep_status, deep_gaps = self._path_to_endpoint(entry_ids, endpoint_id, deep_depth)
                if deep_status == "resolved":
                    chain, path_edges, status, gaps = deep_chain, deep_edges, deep_status, []
                else:
                    gaps.extend(deep_gaps)
            chain, status, normalization_warnings, loop_detected = _normalize_ranked_path_chain(
                entry_ids,
                endpoint_id,
                chain,
                status,
            )
            if normalization_warnings:
                gaps.extend(normalization_warnings)
            reachability = 0.25 if status == "resolved" and chain and chain[0] in entry_ids else 0.10 if chain else 0.0
            unresolved_edges = sum(1 for edge in path_edges if edge.confidence < .50)
            input_control = 0.20 if svc.input_control.get(endpoint_id) else 0.0
            if risk_signals and any(set(sig.parameter_dependencies) & set(svc.input_control.get(endpoint_id, {})) for sig in risk_signals):
                input_control = 0.20
            elif svc.input_control.get(endpoint_id):
                input_control = 0.12
            desc_match = 0.0
            if endpoint_id in ref_symbol_ids or symbol.file in ref_files:
                desc_match = 0.15
            elif symbol.name.casefold() in suspect_funcs or symbol.file.casefold() in suspect_files:
                desc_match = 0.04
            harness_alignment = 0.0
            if harness_first_hops:
                chain_names = {by_id[sid].name.casefold() for sid in chain if sid in by_id}
                if chain_names & harness_first_hops:
                    harness_alignment = 0.10
            elif chain and chain[0] in entry_ids:
                harness_alignment = 0.04
            penalty = 0.0
            if unresolved_edges:
                penalty += min(.12, .04 * unresolved_edges)
            if status != "resolved":
                penalty += .10
            if len(chain) <= 2 and risk_semantics < .20:
                penalty += .06
            if not risk_signals or best_signal.kind == "path_anchor":
                penalty += .05
            event_role = _event_role_for_signal(best_signal)
            event_pair: dict[str, Any] = {}
            paired_endpoint: dict[str, Any] = {}
            false_positive_guards = list(best_role_schema.false_positive_guards or semantics.false_positive_guards or [])
            if required_pairs:
                left, right = required_pairs[0]
                if event_role in {left, right}:
                    needed = right if event_role == left else left
                    paired_endpoint = self._paired_endpoint_for_role(
                        endpoint_id=endpoint_id,
                        needed_role=needed,
                        semantics_family=semantics.family,
                        summaries=summaries,
                        by_id=by_id,
                    )
                    event_pair = {
                        "required": [left, right],
                        "current_event": event_role,
                        "missing_event": "" if paired_endpoint else needed,
                        "paired": bool(paired_endpoint),
                    }
                    if not paired_endpoint:
                        gaps.append({
                            "id": f"missing_{needed}_endpoint",
                            "reason": f"{semantics.family} candidate needs paired {needed} endpoint for source-backed event pair",
                        })
            total = max(0.0, min(1.0, reachability + risk_semantics + input_control + desc_match + harness_alignment + 0.12 * role_score - penalty))
            role = best_role_schema.role if role_score >= .30 else classify_endpoint_role(best_signal, semantics)
            endpoint_key = (endpoint_id, role)
            if endpoint_key in endpoint_seen and len(pool) >= 50:
                continue
            endpoint_seen.add(endpoint_key)
            callsite_material = [edge.callsite_id for edge in path_edges]
            path_material = json.dumps([svc.graph_id, chain, callsite_material, best_signal.signal_id], sort_keys=True)
            path_id = "vpath_" + hashlib.blake2s(path_material.encode(), digest_size=8).hexdigest()
            chain_items = []
            for sid in chain:
                sym = by_id.get(sid)
                if sym:
                    chain_items.append({
                        "symbol_id": sid, "function": sym.qualified_name,
                        "file": sym.file, "line": sym.body_location.start_line,
                    })
            channels = ["entry_forward"]
            if endpoint_id in ref_symbol_ids or symbol.file in ref_files:
                channels.append("verified_description")
            if risk_semantics > .15:
                channels.append("risk_backward")
            if harness_alignment:
                channels.append("harness_first_hop")
            if event_pair.get("paired") and "event_pair_completion" not in channels:
                channels.append("event_pair_completion")
            pool.append(RankedVulnerabilityPath(
                path_id=path_id,
                symbol_ids=chain,
                endpoint_symbol_id=endpoint_id,
                endpoint_signal_id=best_signal.signal_id,
                endpoint_role=role,
                candidate_family=semantics.family,
                score=round(total, 4),
                score_breakdown={
                    "reach": round(reachability, 3),
                    "risk": round(risk_semantics, 3),
                    "input": round(input_control, 3),
                    "desc": round(desc_match, 3),
                    "harness": round(harness_alignment, 3),
                    "role": round(role_score, 3),
                    "penalty": round(penalty, 3),
                },
                resolution_status=status,
                description_ref_ids=ref_by_symbol.get(endpoint_id, []),
                graph_distance_hint=max(0, len(chain) - 1) if chain else None,
                gaps=gaps,
                generation_channels=channels,
                role_score=round(role_score, 4),
                event_pair=event_pair,
                diversity_key=f"{symbol.file}:{role}:{semantics.family}",
                false_positive_guards=false_positive_guards,
                normalization_warnings=normalization_warnings,
                loop_detected=loop_detected,
                paired_endpoint=paired_endpoint,
                chain=chain_items,
                endpoint={
                    "symbol_id": endpoint_id,
                    "function": symbol.qualified_name,
                    "file": symbol.file,
                    "line": symbol.body_location.start_line,
                    "signal": {
                        "signal_id": best_signal.signal_id,
                        "kind": best_signal.kind,
                        "expression": best_signal.expression,
                        "severity": best_signal.severity,
                        "reason": best_signal.reason,
                    },
                },
                next_read={
                    "path": symbol.file,
                    "offset": max(0, symbol.body_location.start_line - 20),
                    "limit": min(180, max(80, symbol.body_location.end_line - symbol.body_location.start_line + 40)),
                },
            ))

        pool.sort(key=lambda item: (-item.score, item.endpoint["file"], item.endpoint["line"]))
        selected: list[RankedVulnerabilityPath] = []
        file_counts: dict[str, int] = {}
        role_counts: dict[str, int] = {}
        for item in pool:
            file_name = str(item.endpoint.get("file", ""))
            if file_counts.get(file_name, 0) >= 2:
                continue
            if role_counts.get(item.endpoint_role, 0) >= 3:
                continue
            selected.append(item)
            file_counts[file_name] = file_counts.get(file_name, 0) + 1
            role_counts[item.endpoint_role] = role_counts.get(item.endpoint_role, 0) + 1
            if len(selected) >= top_k:
                break
        if len(selected) < top_k:
            for item in pool:
                if item not in selected:
                    selected.append(item)
                    if len(selected) >= top_k:
                        break
        result = {
            "status": "success" if selected else "partial",
            "paths": [stable_value(item) for item in selected],
            "endpoint_count": len(pool),
            "truncated": len(pool) > len(selected),
            "gaps": entry_gaps,
            "graph_id": svc.graph_id,
            "crash_type": crash_type,
            "candidate_pool_size": min(len(pool), 50),
        }
        fingerprint = hashlib.sha256(json.dumps({
            "graph": svc.graph_id,
            "paths": [item.path_id for item in selected],
            "pool": len(pool),
        }, sort_keys=True).encode()).hexdigest()
        svc.store.put("ranked_vulnerability_paths", fingerprint, result)
        return result

    def reachable_functions_from_entry(
        self, entrypoint: str = "", limit: int = 20,
        crash_type: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Collect all functions reachable from a fuzz driver entry, filtered
        and ranked by vulnerability relevance.

        Implements the expert method's Layer 2+3: starting from the fuzz driver,
        enumerate reachable functions via BFS, then score by crash-type keywords,
        dangerous operations, description match, and call depth.
        """
        svc = self._service
        svc._ensure(svc.config.automatic_timeout_seconds)
        limit = max(1, min(int(limit or 20), 50))

        # 1. Find entry symbol(s)
        entry_ids: set[str] = set()
        if entrypoint:
            entry_ids = {item.symbol_id for item in svc._symbols_matching(entrypoint)}
        if not entry_ids:
            entry_ids = set(svc.entrypoints)

        if not entry_ids:
            return {"status": "error", "reason": "no entrypoint found"}

        # 2. BFS through call graph, depth up to 8
        adjacency: dict[str, set[str]] = {}
        for edge in svc.edges:
            adjacency.setdefault(edge.caller_id, set()).add(edge.callee_id)

        reachable: dict[str, int] = {}  # symbol_id -> depth
        queue = [(eid, 0) for eid in entry_ids]
        for eid, d in queue:
            if eid in reachable or d > 8:
                continue
            reachable[eid] = d
            for callee in adjacency.get(eid, set()):
                if callee not in reachable:
                    queue.append((callee, d + 1))

        # 3. Score each reachable function
        from .vuln_patterns import CRASH_TYPE_SINK_HINTS, is_entry_point_function
        hints = CRASH_TYPE_SINK_HINTS.get(crash_type, {})
        crash_keywords = hints.get("keywords", {})
        crash_categories = hints.get("vuln_categories", {})
        desc_tokens = {
            token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", description)
            if token.lower() not in {"the", "and", "with", "from", "that", "this", "function"}
        }

        by_id = {item.symbol_id: item for item in svc.symbols}
        summaries = {item.function_id: item for item in svc.summaries}

        candidates: list[dict[str, Any]] = []
        for sid, depth in reachable.items():
            symbol = by_id.get(sid)
            if symbol is None:
                continue
            if is_entry_point_function(symbol.name):
                continue

            summary = summaries.get(sid)
            signals = sorted(summary.risk_signals, key=lambda item: -item.severity) if summary else []

            score = 0.0
            # Crash-type keyword match
            name_lower = symbol.name.lower()
            for kw, boost in crash_keywords.items():
                if kw in name_lower:
                    score += boost
            # Crash-type category match
            for sig in signals:
                if sig.kind in crash_categories:
                    score += crash_categories[sig.kind]
            # Dangerous operation signal
            if signals:
                score += 0.10 * min(signals[0].severity, 1.0)
            # Description token match
            name_tokens = set(re.findall(r"[a-z0-9]+", symbol.qualified_name.lower().replace("_", " ")))
            if desc_tokens & name_tokens:
                score += 0.08
            # Depth bonus: deeper = more likely crash site
            if depth >= 4:
                score += 0.08
            elif depth >= 3:
                score += 0.04
            # Entry depth penalty
            if depth <= 1:
                score -= 0.10

            if score < 0.05:
                continue

            candidates.append({
                "symbol_id": sid,
                "function": symbol.qualified_name,
                "file": symbol.file,
                "line": symbol.body_location.start_line,
                "score": round(score, 4),
                "depth": depth,
                "risk_signals": [{"kind": s.kind, "expression": s.expression, "severity": s.severity} for s in signals[:2]],
                "why": signals[0].reason if signals else f"reachable at depth {depth}",
            })

        # 4. Sort by score, diversify across files
        candidates.sort(key=lambda c: -c["score"])
        # Deduplicate by file: max 3 per file
        file_count: dict[str, int] = {}
        diversified: list[dict[str, Any]] = []
        for c in candidates:
            fc = file_count.get(c["file"], 0)
            if fc < 3:
                diversified.append(c)
                file_count[c["file"]] = fc + 1
            if len(diversified) >= limit:
                break

        return {
            "status": "success",
            "entry_functions": [by_id[eid].name for eid in entry_ids if eid in by_id],
            "total_reachable": len(reachable),
            "candidates": diversified,
            "crash_type": crash_type,
        }

    def analyze_sink_candidate(self, candidate: SinkCandidateInput | dict[str, Any], mode: str = "automatic", budget_profile: str = "default") -> dict[str, Any]:
        svc = self._service
        candidate = candidate if isinstance(candidate, SinkCandidateInput) else SinkCandidateInput(**candidate)
        if not candidate.reason: return {"status": "error", "reason": "candidate.reason is required"}
        if not svc.symbols:
            svc.index_repository(timeout_seconds=svc.config.automatic_timeout_seconds if mode == "automatic" else svc.config.analysis_timeout_seconds, priority_files=[candidate.file])
        if candidate.file and svc._resolve_file_name(candidate.file) not in svc.file_hashes:
            svc.ensure_file_indexed(candidate.file, timeout_seconds=2.0)
        fingerprint = hashlib.sha256(json.dumps({"candidate": stable_value(candidate), "graph_id": svc.graph_id, "files": svc.file_hashes, "grammar": GRAMMAR_VERSION, "analysis": ANALYSIS_VERSION, "config": svc.config.hash(), "mode": mode}, sort_keys=True).encode()).hexdigest()
        cached = svc.store.get("candidate_fingerprint", fingerprint)
        if cached: return {**cached, "cache_hit": True}
        target, target_resolution = self._resolve_target(candidate)
        path_result = svc.find_paths_to_target(target.symbol_id if target else candidate.function or candidate.callee or "", max_depth=svc.config.automatic_max_call_depth if mode == "automatic" else svc.config.max_call_depth, top_k=svc.config.automatic_top_paths if mode == "automatic" else svc.config.max_paths)
        paths = path_result.get("paths", [])
        target_summary = next((s for s in svc.summaries if target and s.function_id == target.symbol_id), None)
        sink_names = [str(value or "") for value in (candidate.callee, candidate.expression, candidate.function) if value]
        sink_calls = [c for c in (target_summary.calls if target_summary else []) if (not sink_names or any(name in c.callee_text for name in sink_names)) and (not candidate.line or abs(c.location.start_line-candidate.line) <= 2)]
        provenance = []
        for call in sink_calls[:1]:
            for index, arg in enumerate(call.arguments):
                trace = svc.trace_value(target.symbol_id, call.location.start_line, arg.render()) if target else {"status": "unresolved"}
                provenance.append({"sink_argument": f"{call.callee_text}.arg{index}", "expression": arg.render(), "status": trace.get("status"), "trace": trace.get("steps", []), "origin": trace.get("origin", "")})
        if not provenance and paths:
            for name, value in paths[0].get("edges", [])[-1].get("bindings", {}).items() if paths[0].get("edges") else []:
                provenance.append({"sink_argument": name, "expression": _expr(value).render(), "status": "partially_resolved", "trace": []})
        from .input_mapping import derive_input_mapping
        input_mappings = []
        for item in provenance[:4]:
            mapping = derive_input_mapping(
                item,
                harness=candidate.metadata.get("harness") if isinstance(candidate.metadata, dict) else None,
                sink_argument=str(item.get("sink_argument") or ""),
                sink_expression=str(item.get("expression") or ""),
                constraint="",
            )
            input_mappings.append(stable_value(mapping))
        requirements: list[dict[str, Any]] = []
        for path in paths:
            for order, item in enumerate(path.get("constraints", []), start=1):
                requirement = _requirement_from_constraint(item, path.get("path_id", ""), order)
                if requirement["expression"] and requirement["expression"] not in {x["expression"] for x in requirements}:
                    requirements.append(requirement)
        trigger_conditions: list[dict[str, Any]] = []
        sink_result: dict[str, Any] = {"status": "not_run", "candidates": []}
        if target:
            from .sink_detector import analyze_sink_file_isolated
            sink_result = analyze_sink_file_isolated(
                svc.root, target.file, sink_function=target.qualified_name,
                line=candidate.line, description=" ".join(filter(None, [candidate.category, candidate.reason])),
                timeout=2.0,
            )
            base_order = len(requirements) + 1
            for offset, item in enumerate(sink_result.get("candidates", [])):
                role = item.get("role", "trigger")
                if role not in {"trigger", "hazard", "reachability", "binding", "structure", "dispatch", "carrier", "avoid"}:
                    role = "trigger"
                sink_origin = dict(item.get("source_span") or {})
                sink_origin.setdefault("file", target.file)
                source_item = {
                    "expression": item.get("required_formula") or item.get("normalized_formula") or item.get("required_condition"),
                    "origin": sink_origin,
                    "reason": item.get("description") or item.get("origin"),
                    "role": role,
                    "gate_type": item.get("gate_type", "value_gate"),
                    "safe_formula": item.get("safe_formula", ""),
                    "violation_formula": item.get("violation_formula", ""),
                    "confidence": item.get("confidence_score", .5),
                    "status": "inferred",
                }
                req = _requirement_from_constraint(source_item, paths[0]["path_id"] if paths else "sink_local", base_order + offset)
                if req["expression"] and req["expression"] not in {x["expression"] for x in requirements}:
                    requirements.append(req)
                if role in {"trigger", "hazard"}:
                    trigger_conditions.append(req)
        gaps: list[dict[str, Any]] = []
        if target is None:
            gaps.append({
                "id": "target_resolution_required", "reason": target_resolution.get("reason"),
                "candidate_symbol_ids": target_resolution.get("candidate_symbol_ids", []),
            })
        elif not paths:
            gaps.append({"id": "caller_path_required", "reason": "no_verified_entry_to_target_path"})
        if target and sink_result.get("status") == "partial" and sink_result.get("reason"):
            gaps.append({"id": "sink_semantics_partial", "reason": sink_result.get("reason")})
        version_key = candidate.candidate_id
        previous = svc.store.get("brief_latest", version_key) or {}; version = int(previous.get("version", 0)) + 1
        full_id = f"analysis_{candidate.candidate_id}_v{version}"; brief_id = f"brief_{candidate.candidate_id}_v{version}"
        alternatives = self._navigation._expand_symbol_neighborhood(target.symbol_id, depth=3, limit=10) if target else []
        if candidate.metadata.get("description_derived") and target:
            current_row = next((item for item in self._navigation._navigation_rows(description=candidate.reason) if item["symbol_id"] == target.symbol_id), None)
            if current_row and current_row.get("evidence", {}).get("description_prior", 0) and current_row.get("score", 0) < .4:
                alternatives.insert(0, {
                    "symbol_id": target.symbol_id, "function": target.qualified_name,
                    "file": target.file, "line": target.body_location.start_line,
                    "diagnosis": "description_anchor", "score": current_row["score"],
                    "why_inspect": "description match is stronger than code-path evidence; validate before selecting",
                })
        mapped_requirements = []
        for mapping in input_mappings:
            if mapping.get("status") in {"confirmed", "inferred"}:
                req = {
                    "requirement_id": "req_" + mapping["mapping_id"],
                    "path_id": paths[0]["path_id"] if paths else "sink_local",
                    "order": len(requirements) + len(mapped_requirements) + 1,
                    "role": "dataflow",
                    "gate_type": "value_gate",
                    "expression": f"{mapping.get('sink_argument')} <- {mapping.get('source_parameter')}[{mapping.get('offset_expression') or '?'}]",
                    "safe_formula": "",
                    "violation_formula": "",
                    "input_mapping": mapping["mapping_id"],
                    "status": mapping.get("status", "inferred"),
                    "confidence": mapping.get("confidence", .5),
                    "origin": stable_value(mapping.get("evidence", [{}])[0]) if mapping.get("evidence") else {},
                    "reason": "sink argument has source-backed input byte mapping",
                }
                mapped_requirements.append(req)
        requirements.extend(mapped_requirements)
        full = {"candidate": stable_value(candidate), "target": stable_value(target) if target else {}, "target_resolution": target_resolution, "paths": paths, "requirements": requirements, "trigger_conditions": trigger_conditions, "sink_dataflow": provenance, "input_mappings": input_mappings, "gaps": gaps, "alternatives": alternatives}
        svc.store.put("analysis", full_id, full)
        def _path_brief(p):
            chain_details = []
            for sid in p["symbol_ids"]:
                sym = next((s for s in svc.symbols if s.symbol_id == sid), None)
                if sym:
                    chain_details.append({"function": sym.name, "file": sym.file, "line": sym.body_location.start_line})
                else:
                    chain_details.append({"function": sid, "file": "", "line": 0})
            return {"path_id": p["path_id"], "chain": [d["function"] for d in chain_details], "chain_details": chain_details, "confidence": p["score"]}
        path_briefs = [_path_brief(p) for p in paths[:svc.config.automatic_top_paths]]
        suggested: list[dict[str, Any]] = []
        brief_mappings = [
            item for item in input_mappings
            if item.get("status") in {"confirmed", "inferred"}
        ][:4] + [
            item for item in input_mappings
            if item.get("status") == "unresolved" and item.get("gaps")
        ][:2]
        brief = SinkAnalysisBrief(
            brief_id, candidate.candidate_id,
            "success" if target and (paths or trigger_conditions) and not gaps else "partial",
            {"symbol_id": target.symbol_id if target else "", "expression": candidate.expression or candidate.callee or candidate.function, "location": f"{candidate.file}:{candidate.line}"},
            path_briefs, requirements[:svc.config.automatic_max_constraints], provenance,
            gaps[:3], suggested, {"call_chain": max((p["score"] for p in paths), default=0.0), "constraints": .8 if requirements else 0.0, "dataflow": .7 if provenance else 0.0},
            {"paths_truncated": path_result.get("truncated", False), "token_budget": svc.config.automatic_token_budget}, full_id,
            target_resolution=target_resolution,
            requirements=requirements[:svc.config.automatic_max_constraints],
            trigger_conditions=trigger_conditions[:6], gaps=gaps[:3],
            alternatives=alternatives[:5],
            input_mappings=brief_mappings,
        )
        brief.context_payload = svc.render_brief(brief)
        while _estimate_tokens(brief.context_payload) > svc.config.automatic_token_budget and (brief.requirements or brief.gaps or brief.alternatives or len(brief.candidate_paths) > 1):
            if brief.gaps: brief.gaps.pop(); brief.unresolved = list(brief.gaps)
            elif brief.alternatives: brief.alternatives.pop()
            elif brief.requirements: brief.requirements.pop(); brief.key_constraints = list(brief.requirements)
            else: brief.candidate_paths.pop()
            brief.truncation["context_budget_reached"] = True; brief.context_payload = svc.render_brief(brief)
        result = {"brief_id": brief.brief_id, "candidate_id": candidate.candidate_id, "status": brief.status, "context_payload": brief.context_payload, "full_result_id": full_id, "brief": stable_value(brief), "cache_hit": False}
        svc.store.put("brief", brief.brief_id, result)
        svc.store.put("brief_latest", version_key, {"version": version, "result": result}); svc.store.put("candidate_fingerprint", fingerprint, result)
        return result
