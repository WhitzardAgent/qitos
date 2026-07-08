"""Navigation and sink-search lead discovery extracted from AnalysisService."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from .models import (
    CallEdge, FunctionSummary, FunctionSymbol, RiskSignal,
    SinkAnalysisBrief, SourceLocation, stable_value,
)

_LOG = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level static helpers (formerly AnalysisService static methods)
# ---------------------------------------------------------------------------

def _description_value(analysis: Any, name: str) -> list[str]:
    value = analysis.get(name, []) if isinstance(analysis, dict) else getattr(analysis, name, [])
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _description_values(analysis: Any, field_name: str) -> list[str]:
    if analysis is None:
        return []
    value = analysis.get(field_name, []) if isinstance(analysis, dict) else getattr(analysis, field_name, [])
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _identifier_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _estimate_tokens(text: str) -> int:
    return max(1, sum(1 if ord(c) > 127 else .25 for c in text))


def _expr_identifiers(expression: Any) -> set[str]:
    values = {str(expression.value)} if expression.kind == "identifier" and expression.value else set()
    for child in expression.children:
        values.update(_expr_identifiers(child))
    return values


# ---------------------------------------------------------------------------
# NavigationService
# ---------------------------------------------------------------------------

class NavigationService:
    """Handles navigation lead discovery, sink search, and neighborhood expansion."""

    def __init__(self, service: Any) -> None:
        self._service = service

    # -- internal helpers ---------------------------------------------------

    def _navigation_rows(self, *, description: str = "", focus_symbol_ids: set[str] | None = None, crash_type: str = "") -> list[dict[str, Any]]:
        svc = self._service
        by_id = {item.symbol_id: item for item in svc.symbols}
        summaries = {item.function_id: item for item in svc.summaries}
        incoming: dict[str, list[CallEdge]] = {}
        outgoing: dict[str, list[CallEdge]] = {}
        for edge in svc.edges:
            incoming.setdefault(edge.callee_id, []).append(edge)
            outgoing.setdefault(edge.caller_id, []).append(edge)
        description_tokens = {
            token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", description)
            if token.lower() not in {"the", "and", "with", "from", "that", "this", "function"}
        }
        utility_tokens = ("mem", "byte", "copy", "insert", "append", "read", "write", "free", "release", "destroy", "convert", "decode")
        rows: list[dict[str, Any]] = []
        for symbol in svc.symbols:
            summary = summaries.get(symbol.symbol_id)
            if summary is None:
                continue
            signals = sorted(summary.risk_signals, key=lambda item: -item.severity)
            controlled = svc.input_control.get(symbol.symbol_id, {})
            reachable = symbol.symbol_id in svc.entry_paths
            if not signals and not reachable and symbol.symbol_id not in (focus_symbol_ids or set()):
                continue
            direct_dependencies = {
                name for signal in signals for name in signal.parameter_dependencies
            } & set(controlled)
            input_score = .30 if direct_dependencies else .22 if controlled else 0.0
            risk_score = .25 * (signals[0].severity if signals else 0.0)
            reach_score = .15 if reachable else 0.0
            direct_score = .10 if signals and signals[0].kind not in {"utility_call", "loop_progress", "input_arithmetic"} else .04 if signals else 0.0
            utility = any(token in symbol.name.lower() for token in utility_tokens) or any(
                item.kind in {"memory_copy", "io", "lifecycle", "allocation", "utility_call"} for item in signals
            )
            utility_score = .10 if utility else 0.0
            focus_score = .05 if symbol.symbol_id in (focus_symbol_ids or set()) else 0.0
            name_tokens = set(re.findall(r"[a-z0-9]+", symbol.qualified_name.lower().replace("_", " ")))
            description_score = .05 if description_tokens & name_tokens else 0.0
            penalty = 0.0
            # Depth-aware scoring: shallow functions (depth<=1) are dispatchers,
            # deep-but-reachable functions (depth>=4) are likely actual crash sites.
            call_depth = len(svc.entry_paths.get(symbol.symbol_id, [])) if reachable else 0
            depth_score = 0.0
            if reachable and svc.entrypoints:
                if call_depth <= 1:
                    penalty += .12  # Too shallow — dispatcher, not crash site
                elif call_depth >= 4:
                    depth_score = .08  # Deep + reachable = likely crash function
                elif call_depth >= 3:
                    depth_score = .04  # Moderately deep
            if not reachable and svc.entrypoints:
                penalty += .15
            if summary.unresolved_nodes:
                penalty += min(.08, .02 * len(summary.unresolved_nodes))
            # Stdlib functions without vulnerability patterns are very unlikely sinks
            from .vuln_patterns import classify_call
            leaf_name = symbol.name.rsplit("::", 1)[-1]
            call_class = classify_call(leaf_name)
            if call_class == "stdlib":
                penalty += .50  # Safe libc — very unlikely to be the sink
            elif call_class == "vuln_stdlib":
                risk_score += .10  # Boost unsafe libc calls (strcpy, memcpy, etc.)
                utility_score += .05
                # Add vuln-specific risk signal if not already present
                from .vuln_patterns import get_vuln_pattern
                vp = get_vuln_pattern(leaf_name)
                if vp is not None and not any(s.kind == "unsafe_api" for s in signals):
                    vuln_signal = RiskSignal(
                        signal_id=f"vuln_{leaf_name}",
                        kind="unsafe_api",
                        severity=float({"critical": .95, "high": .85, "medium": .65, "low": .40}.get(vp.severity, .50)),
                        expression=leaf_name,
                        location=SourceLocation(symbol.file, symbol.body_location.start_line, 0,
                                                symbol.body_location.end_line, 0),
                        reason=f"unsafe {vp.category}: {vp.description} — prefer {vp.safe_alternative}",
                        parameter_dependencies=sorted(controlled),
                    )
                    signals.append(vuln_signal)
            # Entry-point functions are never the actual crash sink
            from .vuln_patterns import is_entry_point_function
            if is_entry_point_function(leaf_name):
                penalty += 0.30
            # Crash-type-aware keyword boosts
            crash_type_boost = 0.0
            if crash_type:
                from .vuln_patterns import CRASH_TYPE_SINK_HINTS
                hints = CRASH_TYPE_SINK_HINTS.get(crash_type)
                if hints:
                    name_lower = symbol.name.lower()
                    for kw, boost in hints["keywords"].items():
                        if kw in name_lower:
                            crash_type_boost += boost
                    cat_boosts = hints.get("vuln_categories", {})
                    for sig in signals:
                        if sig.kind in cat_boosts:
                            crash_type_boost += cat_boosts[sig.kind]
            score = max(0.0, min(1.0, input_score + risk_score + reach_score + direct_score + utility_score + focus_score + description_score + depth_score + crash_type_boost - penalty))
            if score < .10:
                continue
            if signals and signals[0].kind in {"lifecycle", "lifecycle_invalidation", "free"}:
                role = "lifecycle"
            elif signals and direct_score >= .10:
                role = "direct_operation"
            elif utility:
                role = "utility"
            elif len(outgoing.get(symbol.symbol_id, [])) >= 3:
                role = "dispatch"
            else:
                role = "wrapper"
            path_ids = svc.entry_paths.get(symbol.symbol_id, [])
            chain = [by_id[item].name for item in path_ids if item in by_id]
            top_signals = [{
                "signal_id": item.signal_id, "kind": item.kind,
                "expression": item.expression, "severity": item.severity,
                "location": stable_value(item.location), "reason": item.reason,
                "input_dependencies": item.parameter_dependencies,
            } for item in signals[:3]]
            material = f"{svc.graph_id}|{symbol.symbol_id}"
            rows.append({
                "lead_id": "lead_" + hashlib.blake2s(material.encode(), digest_size=7).hexdigest(),
                "symbol_id": symbol.symbol_id, "function": symbol.qualified_name,
                "file": symbol.file, "line": symbol.body_location.start_line,
                "end_line": symbol.body_location.end_line, "score": round(score, 4),
                "role": role, "reachable_from_entry": reachable,
                "input_controlled_parameters": sorted(controlled),
                "input_path": chain, "risk_signals": top_signals,
                "why_inspect": (
                    top_signals[0]["reason"] if top_signals
                    else "reachable from the fuzz entry and useful for following input flow"
                ),
                "next_read": {
                    "path": symbol.file, "offset": max(0, symbol.body_location.start_line - 1),
                    "limit": min(240, max(1, symbol.body_location.end_line - symbol.body_location.start_line + 1)),
                },
                "evidence": {
                    "input_control": round(input_score, 3), "risk": round(risk_score, 3),
                    "reachability": round(reach_score, 3), "directness": round(direct_score, 3),
                    "utility": round(utility_score, 3), "read_focus": round(focus_score, 3),
                    "description_prior": round(description_score, 3), "depth_prior": round(depth_score, 3),
                    "crash_type_prior": round(crash_type_boost, 3),
                    "penalty": round(penalty, 3), "call_depth": call_depth,
                },
                "incoming": [item.caller_id for item in incoming.get(symbol.symbol_id, [])[:4]],
                "outgoing": [item.callee_id for item in outgoing.get(symbol.symbol_id, [])[:4]],
            })
        return sorted(rows, key=lambda item: (-item["score"], -len(item["risk_signals"]), item["file"], item["line"]))

    @staticmethod
    def _diversify_navigation(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        remaining = list(rows)
        while remaining and len(selected) < limit:
            def adjusted(row: dict[str, Any]) -> float:
                penalty = 0.0
                for prior in selected:
                    adjacent = row["symbol_id"] in prior.get("incoming", []) + prior.get("outgoing", [])
                    if adjacent:
                        penalty = max(penalty, .14)
                    elif row["file"] == prior["file"] and row["role"] == prior["role"]:
                        penalty = max(penalty, .08)
                return float(row["score"]) - penalty
            best = max(remaining, key=adjusted)
            selected.append(best)
            remaining.remove(best)
        for row in selected:
            row.pop("incoming", None); row.pop("outgoing", None)
        return selected

    # -- public API ---------------------------------------------------------

    def discover_sink_navigation_leads(
        self, entrypoint: str | None = None, limit: int = 5,
        description: str = "", focus_symbol_ids: list[str] | None = None,
        crash_type: str = "",
    ) -> dict[str, Any]:
        """Return source-backed places to inspect; never claims a true sink."""
        svc = self._service
        svc._ensure(svc.config.automatic_timeout_seconds)
        limit = max(1, min(int(limit or 5), 20))
        rows = self._navigation_rows(description=description, focus_symbol_ids=set(focus_symbol_ids or []), crash_type=crash_type)
        if entrypoint:
            matched = {item.symbol_id for item in svc._symbols_matching(entrypoint)}
            rows = [row for row in rows if not matched or any(item in matched for item in svc.entry_paths.get(row["symbol_id"], [])[:1])]
        leads = self._diversify_navigation(rows, limit)
        mentioned = [item for item in svc.symbols if item.name.lower() in description.lower()]
        lead_ids = {item["symbol_id"] for item in leads}
        warnings = []
        for symbol in mentioned[:5]:
            if symbol.symbol_id not in lead_ids:
                warnings.append({
                    "kind": "description_anchor_stale", "function": symbol.qualified_name,
                    "reason": "description match did not outrank input reachability and direct operation evidence",
                })
        material = json.dumps({"graph": svc.graph_id, "entrypoint": entrypoint, "description": description, "leads": [x["lead_id"] for x in leads]}, sort_keys=True)
        brief_id = "search_" + hashlib.blake2s(material.encode(), digest_size=8).hexdigest()
        entry_names = [next((s.name for s in svc.symbols if s.symbol_id == item), item) for item in svc.entrypoints]
        lines = [
            f"Static navigation brief: {brief_id}",
            "Inspect these navigation leads and explicitly confirm one with record_sink_candidate.",
        ]
        if entry_names:
            lines.append("entries=" + ", ".join(entry_names[:3]))
        for index, lead in enumerate(leads, 1):
            signal = lead["risk_signals"][0] if lead["risk_signals"] else {}
            path = " -> ".join(lead["input_path"][-5:]) if lead["input_path"] else "unverified"
            lines.append(f'{index}. {lead["function"]} @{lead["file"]}:{lead["line"]} role={lead["role"]} score={lead["score"]:.2f}')
            lines.append(f'   input_path={path}; signal={signal.get("kind", "none")}: {signal.get("expression", "")[:160]}')
            lines.append(f'   next_read=READ(path="{lead["file"]}", offset={lead["next_read"]["offset"]}, limit={lead["next_read"]["limit"]})')
        for warning in warnings[:2]:
            lines.append(f'warning={warning["kind"]}: {warning["function"]} — {warning["reason"]}')
        payload = "\n".join(lines)
        while _estimate_tokens(payload) > svc.config.automatic_token_budget and len(leads) > 1:
            leads.pop(); lines = lines[:2 + len(leads) * 3]; payload = "\n".join(lines)
        result = {
            "status": "success" if leads else "partial", "brief_id": brief_id,
            "graph_id": svc.graph_id, "entrypoints": entry_names, "leads": leads,
            "warnings": warnings, "context_payload": payload,
            "truncated": len(rows) > len(leads),
        }
        svc.store.put("sink_search_brief", brief_id, result)
        return result

    def _expand_symbol_neighborhood(self, symbol_id: str, depth: int = 3, limit: int = 10) -> list[dict[str, Any]]:
        svc = self._service
        rows = {item["symbol_id"]: item for item in self._navigation_rows()}
        adjacency: dict[str, set[str]] = {}
        for edge in svc.edges:
            adjacency.setdefault(edge.caller_id, set()).add(edge.callee_id)
            adjacency.setdefault(edge.callee_id, set()).add(edge.caller_id)
        distances = {symbol_id: 0}; queue = [symbol_id]
        while queue:
            current = queue.pop(0)
            if distances[current] >= max(1, min(depth, 6)):
                continue
            for target in adjacency.get(current, set()):
                if target not in distances:
                    distances[target] = distances[current] + 1; queue.append(target)
        current = rows.get(symbol_id, {})
        alternatives = []
        for target_id, distance in distances.items():
            if target_id == symbol_id or target_id not in rows:
                continue
            row = dict(rows[target_id]); relative = "alternate"
            if row.get("role") == "direct_operation" and current.get("role") in {"wrapper", "dispatch"}:
                relative = "possibly_too_shallow"
            elif current.get("role") == "utility" and row.get("role") in {"wrapper", "dispatch"}:
                relative = "possibly_too_deep"
            elif row.get("role") == "direct_operation":
                relative = "direct_hazard_site"
            row.update({"distance": distance, "diagnosis": relative})
            alternatives.append(row)
        return sorted(alternatives, key=lambda item: (-item["score"], item["distance"]))[:max(1, min(limit, 20))]

    def expand_candidate_neighborhood(self, candidate_id: str, depth: int = 3, limit: int = 10) -> dict[str, Any]:
        svc = self._service
        latest = svc.store.get("brief_latest", candidate_id) or {}
        result = latest.get("result", {}) if isinstance(latest, dict) else {}
        symbol_id = str((result.get("brief") or {}).get("target_resolution", {}).get("symbol_id") or "")
        if not symbol_id:
            matches = svc._symbols_matching(candidate_id)
            symbol_id = matches[0].symbol_id if len(matches) == 1 else ""
        if not symbol_id:
            return {"status": "not_found", "candidate_id": candidate_id, "alternatives": []}
        return {"status": "success", "candidate_id": candidate_id, "symbol_id": symbol_id, "alternatives": self._expand_symbol_neighborhood(symbol_id, depth, limit)}

    def mark_navigation_lead_reviewed(self, lead_id: str, outcome: str) -> dict[str, Any]:
        svc = self._service
        if outcome not in {"confirmed", "rejected", "deferred"}:
            return {"status": "error", "reason": "outcome must be confirmed, rejected, or deferred"}
        value = {"status": "success", "lead_id": lead_id, "outcome": outcome, "updated": time.time()}
        svc.store.put("navigation_review", lead_id, value)
        return value

    def get_sink_search_brief(self, brief_id: str) -> dict[str, Any]:
        svc = self._service
        return svc.store.get("sink_search_brief", brief_id) or {"status": "not_found", "brief_id": brief_id}

    def analyze_read_context(
        self,
        file: str,
        start_line: int,
        end_line: int,
        *,
        brief: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map a model-selected READ range to the immutable full-file graph."""
        svc = self._service
        svc._ensure(svc.config.automatic_timeout_seconds)
        rel = svc._resolve_file_name(file)
        if rel not in svc.file_hashes:
            fill = svc.ensure_file_indexed(rel, timeout_seconds=2.0)
            if fill.get("status") in {"error", "not_found"}:
                return {"status": "no_change", "reason": fill.get("reason", "file_not_indexed")}
        focus = [
            symbol for symbol in svc.symbols
            if symbol.file == rel
            and symbol.body_location.start_line <= max(start_line, end_line)
            and symbol.body_location.end_line >= min(start_line, end_line)
        ]
        if not focus:
            return {"status": "no_change", "reason": "read_range_has_no_function"}
        focus_ids = {item.symbol_id for item in focus}
        by_id = {item.symbol_id: item for item in svc.symbols}
        caller_names: list[str] = []
        callee_names: list[str] = []
        guards: list[str] = []
        focus_signals: list[dict[str, Any]] = []
        for edge in svc.edges:
            if edge.callee_id in focus_ids and edge.caller_id in by_id:
                caller_names.append(by_id[edge.caller_id].name)
            if edge.caller_id in focus_ids and edge.callee_id in by_id:
                callee_names.append(by_id[edge.callee_id].name)
        for summary in svc.summaries:
            if summary.function_id not in focus_ids:
                continue
            focus_signals.extend({
                "signal_id": item.signal_id, "kind": item.kind,
                "expression": item.expression, "severity": item.severity,
                "location": stable_value(item.location), "reason": item.reason,
            } for item in summary.risk_signals[:3])
            for call in summary.calls:
                guards.extend(item.normalized_text for item in call.local_guards if item.confidence >= .5)
        paths = (brief or {}).get("candidate_paths", [])
        selected_path = next((path for path in paths if any(
            detail.get("function") in {item.name for item in focus}
            and detail.get("file") == rel
            for detail in path.get("chain_details", [])
        )), None)
        # Classify callees by risk (common crash-site patterns)
        _RISKY_PATTERNS = re.compile(
            r"memcpy|memmove|memset|realloc|calloc|malloc|free|"
            r"bebytes2|lebytes2|"
            r"decode|parse|read|write|copy|convert|compress|decompress|"
            r"alloc|destroy|release|insert|remove|append|push|pop|"
            r"strcpy|strcat|strncpy|sprintf|vsprintf|scanf|sscanf|gets|fgets",
            re.IGNORECASE,
        )
        # Count sub-callees per callee for depth annotation (leaf = 0 = crash site)
        callee_sub_count: dict[str, int] = {}
        for cr_name in dict.fromkeys(callee_names):
            callee_sym = next((s for s in svc.symbols if s.name == cr_name), None)
            if callee_sym:
                callee_sub_count[cr_name] = sum(
                    1 for e in svc.edges if e.caller_id == callee_sym.symbol_id
                )
            else:
                callee_sub_count[cr_name] = -1
        callee_risks = [
            {
                "name": n,
                "risk": "risky" if _RISKY_PATTERNS.search(n) else "normal",
                "sub_callees": callee_sub_count.get(n, -1),
            }
            for n in dict.fromkeys(callee_names)
        ][:6]
        base = {
            "focus": [{
                "symbol_id": item.symbol_id, "function": item.qualified_name,
                "file": item.file, "start_line": item.body_location.start_line,
                "end_line": item.body_location.end_line,
            } for item in focus[:3]],
            "direct_callers": list(dict.fromkeys(caller_names))[:3],
            "relevant_callees": [c["name"] for c in callee_risks],  # backward compat
            "callee_risks": callee_risks,
            "notable_guards": list(dict.fromkeys(guards))[:3],
            "risk_signals": sorted(focus_signals, key=lambda item: -item["severity"])[:5],
            "input_controlled_parameters": {
                by_id[sid].qualified_name: sorted(svc.input_control.get(sid, {}))
                for sid in focus_ids if sid in by_id and svc.input_control.get(sid)
            },
        }
        navigation = self.discover_sink_navigation_leads(
            limit=5, focus_symbol_ids=list(focus_ids),
        )
        related_leads = [
            item for item in navigation.get("leads", [])
            if item["symbol_id"] in focus_ids
            or item["symbol_id"] in {edge.callee_id for edge in svc.edges if edge.caller_id in focus_ids}
            or item["symbol_id"] in {edge.caller_id for edge in svc.edges if edge.callee_id in focus_ids}
        ][:3]
        base["navigation_leads"] = related_leads
        base["focus_role"] = related_leads[0].get("role", "structural") if related_leads else "structural"
        if selected_path:
            payload = {
                "status": "success", "kind": "static_analysis_delta",
                "path_id": selected_path.get("path_id", ""), **base,
                "added": [f"[reachability] {item}" for item in base["notable_guards"]],
                "revised": [], "invalidated": [],
            }
        else:
            entry_names = {"LLVMFuzzerTestOneInput", "LLVMFuzzerTestOneInputEx", "main"}
            reachable = any(item.symbol_id in svc.entry_paths for item in focus)
            payload = {"status": "success", "kind": "code_index_context", "reachable_from_entry": reachable, **base}
        fingerprint_material = json.dumps({"graph": svc.graph_id, "payload": payload}, sort_keys=True)
        payload["fingerprint"] = hashlib.sha256(fingerprint_material.encode()).hexdigest()
        return payload
