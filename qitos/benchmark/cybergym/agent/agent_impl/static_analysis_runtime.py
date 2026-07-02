"""Automatic Sink Enrichment integration for CyberGymAgent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from ..analysis.models import SinkCandidateInput
from ..analysis.service import AnalysisService


class StaticAnalysisRuntimeMixin:
    def _analysis_service(self, state: Any) -> AnalysisService | None:
        repo = str(getattr(state, "repo_dir", "") or "")
        if not repo or not Path(repo).is_dir():
            return None
        services = getattr(self, "_static_analysis_services", None)
        if services is None:
            services = self._static_analysis_services = {}
        key = str(Path(repo).resolve())
        if key not in services:
            services[key] = AnalysisService(repo, workspace_root=getattr(state, "workspace_root", "") or Path(repo).parent)
        return services[key]

    def _bootstrap_analysis_index(self, state: Any) -> None:
        service = self._analysis_service(state)
        if service is None:
            state.analysis_index_status = "NO_INDEX"
            return
        priorities = list(getattr(state, "source_files_mentioned", []) or [])
        priorities.extend(
            str(getattr(item, "source_path", "") or "")
            for item in list(getattr(state, "harness_candidates", []) or [])
        )
        priorities.extend(
            str(getattr(item, "file", "") or "")
            for item in list(getattr(state, "sink_candidates", []) or [])
        )
        report = service.index_repository(
            timeout_seconds=service.config.automatic_timeout_seconds,
            priority_files=[item for item in priorities if item],
        )
        state.analysis_graph_id = str(report.get("graph_id") or "")
        state.analysis_index_status = str(report.get("index_status") or "PARTIAL_INDEX")
        state.analysis_index_coverage = {
            key: report.get(key)
            for key in (
                "files_total", "files_indexed", "functions", "callsites",
                "unresolved_callsites", "partial_files", "entrypoints", "sccs",
                "elapsed_ms", "reason",
            )
            if key in report
        }
        search = service.discover_sink_navigation_leads(
            limit=5,
            description=str(getattr(state, "vulnerability_description", "") or ""),
        )
        self._sync_navigation_leads(state, search)

    @staticmethod
    def _sync_navigation_leads(state: Any, result: dict[str, Any]) -> None:
        """Keep Top-3 provisional leads in State without satisfying checkpoints."""
        if result.get("status") not in {"success", "partial"}:
            return
        from ..state import SinkCandidate

        leads = list(result.get("leads") or [])[:5]
        state.sink_search_leads = leads
        state.latest_sink_search_brief = dict(result)
        state.latest_sink_search_brief_id = str(result.get("brief_id") or "")
        fingerprint = hashlib.sha256(json.dumps({
            "brief_id": result.get("brief_id"),
            "leads": [(item.get("lead_id"), item.get("score")) for item in leads],
            "warnings": result.get("warnings"),
        }, sort_keys=True, default=str).encode()).hexdigest()
        state.sink_search_fingerprint = fingerprint
        current_static_ids = {str(item.get("lead_id") or "") for item in leads[:3]}
        state.sink_candidates = [
            item for item in state.sink_candidates
            if item.source != "static_navigation" or item.candidate_id in current_static_ids
        ]
        existing_by_id = {item.candidate_id: item for item in state.sink_candidates}
        lead_functions = {str(item.get("function") or "").rsplit("::", 1)[-1].lower() for item in leads}
        for candidate in state.sink_candidates:
            if candidate.source not in {"description", "description_symbol", "harness_chain"}:
                continue
            candidate.metadata = dict(candidate.metadata or {})
            if candidate.function.lower() not in lead_functions:
                candidate.metadata["description_anchor_stale"] = True
                candidate.confidence = min(candidate.confidence, .29)
            else:
                candidate.metadata["graph_validated"] = True
                candidate.confidence = min(candidate.confidence, .49)
        for rank, lead in enumerate(leads[:3], 1):
            lead_id = str(lead.get("lead_id") or "")
            if not lead_id:
                continue
            candidate = existing_by_id.get(lead_id)
            signal = (lead.get("risk_signals") or [{}])[0]
            evidence = str(lead.get("why_inspect") or signal.get("reason") or "static navigation evidence")
            metadata = {
                "requires_review": True, "navigation_rank": rank,
                "role": lead.get("role"), "symbol_id": lead.get("symbol_id"),
                "next_read": lead.get("next_read"), "input_path": lead.get("input_path"),
                "risk_signals": lead.get("risk_signals", []),
            }
            if candidate is None:
                candidate = SinkCandidate(
                    function=str(lead.get("function") or ""),
                    location=f'{lead.get("file", "")}:{lead.get("line", 0)}',
                    confidence=min(.69, float(lead.get("score") or 0)),
                    evidence=evidence, status="provisional", source="static_navigation",
                    candidate_id=lead_id, file=str(lead.get("file") or ""),
                    line=int(lead.get("line") or 0), reason=evidence, metadata=metadata,
                )
                state.sink_candidates.append(candidate)
                existing_by_id[lead_id] = candidate
            elif candidate.source == "static_navigation":
                candidate.confidence = min(.69, float(lead.get("score") or 0))
                candidate.evidence = candidate.reason = evidence
                candidate.metadata.update(metadata)

    @staticmethod
    def _validate_description_symbols(state: Any, content: str, service: AnalysisService) -> None:
        """Validate description-derived names against the graph without activating them."""
        del content  # The task description is already represented in State.
        by_name: dict[str, list[Any]] = {}
        for symbol in service.symbols:
            by_name.setdefault(symbol.name.lower(), []).append(symbol)
            by_name.setdefault(symbol.qualified_name.lower(), []).append(symbol)
        summaries = {item.function_id: item for item in service.summaries}
        for candidate in list(getattr(state, "sink_candidates", []) or []):
            if candidate.source not in {"description", "description_symbol", "harness_chain"}:
                continue
            matches = by_name.get(candidate.function.lower(), [])
            candidate.metadata = dict(candidate.metadata or {})
            candidate.metadata["description_derived"] = True
            if len(matches) != 1:
                candidate.metadata["description_anchor_stale"] = True
                candidate.confidence = min(candidate.confidence, .29)
                continue
            symbol = matches[0]
            summary = summaries.get(symbol.symbol_id)
            candidate.metadata.update({
                "graph_validated": True, "symbol_id": symbol.symbol_id,
                "reachable_from_entry": symbol.symbol_id in service.entry_paths,
                "risk_signal_count": len(summary.risk_signals) if summary else 0,
            })
            candidate.confidence = min(candidate.confidence, .49)

    def _analyze_read_context(self, state: Any, output: Any) -> None:
        if not isinstance(output, dict) or output.get("status", "success") != "success":
            return
        path = str(output.get("path") or "").strip()
        if not path:
            return
        service = self._analysis_service(state)
        if service is None:
            return
        if not service.symbols:
            self._bootstrap_analysis_index(state)
        offset = max(0, int(output.get("offset") or 0))
        content = str(output.get("content") or "")
        lines = content.splitlines()
        if lines and lines[0].startswith("// Lines "):
            lines = lines[1:]
        start_line = offset + 1
        end_line = offset + max(1, len(lines))
        # ── Description symbol validation ──
        if path.endswith("description.txt"):
            self._validate_description_symbols(state, content, service)
        result = service.analyze_read_context(
            path, start_line, end_line,
            brief=dict(getattr(state, "latest_sink_analysis_brief", {}) or {}),
        )
        if result.get("status") != "success":
            return
        fingerprint = str(result.get("fingerprint") or "")
        if fingerprint and fingerprint == getattr(state, "latest_read_analysis_fingerprint", ""):
            return
        state.latest_read_analysis = result
        state.latest_read_analysis_fingerprint = fingerprint
        state.analysis_graph_id = service.graph_id or getattr(state, "analysis_graph_id", "")
        state.analysis_index_status = service.index_status
        navigation = service.discover_sink_navigation_leads(
            limit=5,
            description=str(getattr(state, "vulnerability_description", "") or ""),
            focus_symbol_ids=[str(item.get("symbol_id") or "") for item in result.get("focus", [])],
        )
        self._sync_navigation_leads(state, navigation)

    def _validate_description_symbols(self, state: Any, description_text: str, service: AnalysisService) -> None:
        """Check if symbols mentioned in description.txt exist in the graph.

        When description-referenced symbols are missing, inject a warning so
        the LLM doesn't waste steps chasing non-existent code.
        """
        if not service.symbols or getattr(state, "_description_validated", False):
            return
        state._description_validated = True
        # Extract candidate symbol names: CamelCase splits and snake_case parts
        import re
        # Find quoted identifiers and CamelCase/snake_case words
        candidates = set()
        for m in re.finditer(r'["\x60](\w{3,})["\x27\x60]', description_text):
            candidates.add(m.group(1))
        # Also split CamelCase: "searchObjectForKeyRec" → ["search", "Object", "For", "Key", "Rec"]
        for word in re.findall(r'[A-Z][a-z]+(?:[A-Z][a-z]+)+', description_text):
            candidates.add(word)
        # Also find snake_case words
        for word in re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', description_text):
            if len(word) >= 4:
                candidates.add(word)
        if not candidates:
            return
        # Check each against the graph's symbol names
        symbol_names = {s.name for s in service.symbols} | {s.qualified_name for s in service.symbols}
        not_found = []
        found = []
        for sym in sorted(candidates):
            if any(sym in sn for sn in symbol_names):
                found.append(sym)
            else:
                not_found.append(sym)
        if not_found:
            hints = list(getattr(state, "metadata", {}).get("_desc_validation_hints", []) or [])
            not_found_str = ", ".join(not_found[:5])
            found_str = ", ".join(found[:3]) if found else "none"
            hints.append(
                f"[DESCRIPTION VALIDATION] Symbols NOT found in repository: {not_found_str}. "
                f"Symbols found: {found_str}. "
                "Prefer harness-based call-chain tracing over description-directed search."
            )
            state.metadata["_desc_validation_hints"] = hints

    @staticmethod
    def _candidate_payload(state: Any, candidate: Any) -> SinkCandidateInput:
        file_name = str(getattr(candidate, "file", "") or "")
        repo = Path(str(getattr(state, "repo_dir", "") or "."))
        if file_name:
            try:
                file_name = Path(file_name).resolve().relative_to(repo.resolve()).as_posix() if Path(file_name).is_absolute() else file_name.removeprefix("repo-vul/")
            except Exception:
                pass
        return SinkCandidateInput(
            candidate_id=str(getattr(candidate, "candidate_id", "") or "sink_runtime"),
            repository_id=str(getattr(candidate, "repository_id", "repo_current") or "repo_current"),
            file=file_name,
            line=int(getattr(candidate, "line", 0) or 0),
            function=str(getattr(candidate, "function", "") or "") or None,
            callee=str(getattr(candidate, "callee", "") or "") or None,
            expression=str(getattr(candidate, "expression", "") or "") or None,
            category=str(getattr(candidate, "category", "") or "") or None,
            reason=str(getattr(candidate, "reason", "") or getattr(candidate, "evidence", "") or "agent proposed target"),
            agent_confidence=float(getattr(candidate, "confidence", .5) or .5),
            related_cve=str(getattr(candidate, "related_cve", "") or "") or None,
            metadata=dict(getattr(candidate, "metadata", {}) or {}),
        )

    def _run_pending_sink_analysis(self, state: Any) -> None:
        candidate_id = str(state.metadata.pop("_pending_sink_analysis", "") or "")
        if not candidate_id:
            return
        candidate = next((c for c in state.sink_candidates if c.candidate_id == candidate_id), None)
        service = self._analysis_service(state)
        if candidate is None or service is None:
            state.analysis_status = "partial"
            return
        state.analysis_status = "AUTO_ANALYSIS_RUNNING"
        try:
            result = service.analyze_sink_candidate(self._candidate_payload(state, candidate), mode="automatic")
            state.latest_sink_analysis_brief = dict(result.get("brief") or {})
            state.latest_brief_id = str(result.get("brief_id") or "")
            state.active_sink_candidate_id = candidate_id
            state.open_analysis_unresolved_ids = [str(x.get("id") or x.get("reason") or "") for x in state.latest_sink_analysis_brief.get("unresolved", [])]
            paths = state.latest_sink_analysis_brief.get("candidate_paths", [])
            state.selected_analysis_path_id = str(paths[0].get("path_id") or "") if paths else ""
            state.analysis_status = "BRIEF_AVAILABLE"
            state.analysis_index_status = "TARGET_ANALYZED"
            state.latest_analysis_mode = "automatic"
            # Auto-populate chain nodes and constraints from the brief
            self._populate_chain_nodes_from_brief(state)
            self._populate_constraints_from_brief(state)
        except Exception as exc:
            state.analysis_status = "partial"
            # Preserve the last valid recipe.  A transient worker or parser
            # failure must not erase already source-backed evidence.
            if not getattr(state, "latest_sink_analysis_brief", None):
                state.latest_sink_analysis_brief = {
                    "status": "partial",
                    "gaps": [{
                        "id": "analysis_error", "reason": f"{type(exc).__name__}: {exc}",
                        "next_query": {"tool": "analyze_sink_candidate", "arguments": {"candidate_id": candidate_id}},
                    }],
                    "unresolved": [],
                }

    @staticmethod
    def _populate_chain_nodes_from_brief(state: Any) -> None:
        """Auto-populate ChainNode entries from the SinkAnalysisBrief's discovered paths."""
        from ..state import ChainNode

        brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
        paths = brief.get("candidate_paths", [])
        if not paths:
            return

        sink_id = str(getattr(state, "active_sink_id", "") or "")
        existing_keys = {
            f"{n.function}@{n.location}@{n.sink_id}"
            for n in getattr(state, "call_chain_nodes", []) or []
        }
        # Track max order per sink_id for ordering
        max_order = max(
            (n.order for n in (state.call_chain_nodes or []) if n.sink_id == sink_id),
            default=-1,
        )

        # Use the top-scoring path
        top_path = paths[0]
        chain_details = top_path.get("chain_details", [])
        path_id = top_path.get("path_id", "")

        auto_nodes = 0
        for i, entry in enumerate(chain_details):
            func = str(entry.get("function", "")).strip()
            if not func or func.startswith("analysis_"):
                continue
            file_name = str(entry.get("file", "")).strip()
            line_no = int(entry.get("line", 0) or 0)
            location = f"{file_name}:{line_no}" if file_name and line_no else file_name

            key = f"{func}@{location}@{sink_id}"
            if key in existing_keys:
                continue

            # Infer role
            if i == 0:
                role = "entry"
            elif i == len(chain_details) - 1:
                role = "sink"
            elif any(kw in func.lower() for kw in ("parse", "read", "decode", "process")):
                role = "parser"
            elif any(kw in func.lower() for kw in ("dispatch", "route", "handle", "select")):
                role = "dispatch"
            else:
                role = "parser"

            max_order += 1
            state.call_chain_nodes.append(ChainNode(
                location=location,
                function=func,
                role=role,
                description="Auto-discovered via interprocedural analysis",
                status="inferred",
                evidence=f"analyze_sink_candidate path_id={path_id}",
                order=max_order,
                sink_id=sink_id,
            ))
            existing_keys.add(key)
            auto_nodes += 1
            if auto_nodes >= 8:
                break

        # Cap total nodes
        if len(state.call_chain_nodes) > 20:
            state.call_chain_nodes = state.call_chain_nodes[-20:]

        # ── Auto-Deepen ──
        # If the deepest function in the top path differs from the recorded
        # sink candidate, auto-append it as a low-confidence candidate so the
        # LLM sees it without needing an explicit tool call.
        if chain_details:
            deepest = chain_details[-1]
            deepest_func = str(deepest.get("function", "")).strip()
            recorded_func = ""
            for c in (getattr(state, "sink_candidates", []) or []):
                if c.candidate_id == sink_id or c.status != "eliminated":
                    recorded_func = c.function
                    break
            if (deepest_func
                    and deepest_func != recorded_func
                    and not any(c.function == deepest_func and c.status != "eliminated"
                                for c in (getattr(state, "sink_candidates", []) or []))):
                from ..state import SinkCandidate
                deepest_file = str(deepest.get("file", "")).strip()
                deepest_line = int(deepest.get("line", 0) or 0)
                # Check if the original candidate was description-derived
                is_desc_anchored = any(
                    c.function == recorded_func
                    and (c.metadata.get("description_derived") or c.metadata.get("description_anchor_stale"))
                    for c in (getattr(state, "sink_candidates", []) or [])
                )
                deepen_confidence = 0.7 if is_desc_anchored else 0.5
                state.sink_candidates.append(SinkCandidate(
                    function=deepest_func,
                    location=f"{deepest_file}:{deepest_line}" if deepest_file and deepest_line else deepest_file,
                    confidence=deepen_confidence,
                    evidence=f"Auto-deepened from graph path: called by {recorded_func}",
                    status="provisional",
                    source="static_navigation",
                    candidate_id="lead_" + hashlib.blake2s(
                        f"{deepest_file}|{deepest_line}|{deepest_func}".encode(), digest_size=7
                    ).hexdigest(),
                    file=deepest_file,
                    line=deepest_line,
                    reason=f"Deepest function in entry→sink path (called by {recorded_func})",
                    metadata={"requires_review": True, "role": "path_alternative"},
                ))
                # Build actionable next-read instruction
                next_read_hint = ""
                if deepest_file:
                    offset = max(0, (deepest_line or 1) - 5)
                    next_read_hint = f" READ: READ(path=\"{deepest_file}\", offset={offset}, limit=40)"
                # Inject a directive hint into the observation metadata
                hints = list(getattr(state, "metadata", {}).get("_auto_deepen_hints", []) or [])
                if is_desc_anchored:
                    hints.append(
                        f"[GRAPH] Sink candidate '{recorded_func}' came from the description, but "
                        f"'{deepest_func}' is the actual leaf sink. Record '{deepest_func}' instead."
                        f"{next_read_hint}"
                    )
                else:
                    hints.append(
                        f"[GRAPH] Deeper sink detected: {deepest_func} (called by {recorded_func}). "
                        f"Consider upgrading.{next_read_hint}"
                    )
                state.metadata["_auto_deepen_hints"] = hints

    @staticmethod
    def _populate_constraints_from_brief(state: Any) -> None:
        """Convert typed recipe requirements without text classification."""
        brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
        requirements = brief.get("requirements") or brief.get("key_constraints") or []
        if not requirements:
            return
        existing_formulas = {
            (s.get("normalized_formula", ""), s.get("path_id", ""))
            for s in (getattr(state, "suggested_constraints", []) or [])
        }
        suggestions = list(getattr(state, "suggested_constraints", []) or [])
        for c in requirements[:12]:
            expr = str(c.get("expression", "")).strip()
            if not expr:
                continue
            path_id = str(c.get("path_id") or getattr(state, "selected_analysis_path_id", "") or "")
            reason = str(c.get("reason", "")).strip()
            origin = c.get("origin") or {}
            dedup_key = (expr, path_id)
            if dedup_key in existing_formulas:
                continue
            node_function = str(origin.get("function", "") if isinstance(origin, dict) else "").strip()
            desc = f"Interprocedural: {expr}"
            if reason:
                desc += f" ({reason})"
            confidence = float(c.get("confidence", .5) or .5)
            suggestions.append({
                "requirement_id": c.get("requirement_id", ""),
                "gate_type": c.get("gate_type", "path_gate"),
                "description": desc,
                "required_condition": expr,
                "normalized_formula": expr,
                "safe_formula": c.get("safe_formula", ""),
                "violation_formula": c.get("violation_formula", ""),
                "input_mapping": c.get("input_mapping", ""),
                "polarity": "satisfy",
                "origin": "interprocedural_analysis",
                "source_span": dict(origin) if isinstance(origin, dict) else {},
                "node_function": node_function,
                "role": c.get("role", "reachability"),
                "path_id": path_id,
                "confidence": "high" if confidence >= .8 else "medium" if confidence >= .5 else "low",
                "analysis_status": c.get("status", "inferred"),
            })
            existing_formulas.add(dedup_key)
        state.suggested_constraints = suggestions[-24:]

    def _deepen_sink_analysis(self, state: Any) -> None:
        """Re-run sink analysis with interactive mode after repeated PoC failures."""
        candidate_id = getattr(state, "active_sink_candidate_id", "")
        if not candidate_id:
            return
        candidate = next((c for c in state.sink_candidates if c.candidate_id == candidate_id), None)
        service = self._analysis_service(state)
        if candidate is None or service is None:
            return
        try:
            result = service.analyze_sink_candidate(
                self._candidate_payload(state, candidate), mode="interactive"
            )
            state.latest_sink_analysis_brief = dict(result.get("brief") or {})
            state.latest_brief_id = str(result.get("brief_id") or "")
            state.latest_analysis_mode = "interactive"
            paths = state.latest_sink_analysis_brief.get("candidate_paths", [])
            state.selected_analysis_path_id = str(paths[0].get("path_id", "")) if paths else ""
            state.analysis_status = "BRIEF_AVAILABLE"
            # Re-populate with deeper results
            self._populate_chain_nodes_from_brief(state)
            self._populate_constraints_from_brief(state)
        except Exception:
            pass  # Keep existing brief on failure

    @staticmethod
    def _inject_static_analysis_brief(state: Any, prepared: str) -> str:
        additions: list[str] = []
        graph_id = str(getattr(state, "analysis_graph_id", "") or "")
        index_status = str(getattr(state, "analysis_index_status", "NO_INDEX") or "NO_INDEX")
        coverage = dict(getattr(state, "analysis_index_coverage", {}) or {})
        index_fp = hashlib.sha256(json.dumps({"graph": graph_id, "status": index_status, "coverage": coverage}, sort_keys=True).encode()).hexdigest()
        if graph_id and index_fp != getattr(state, "injected_index_fingerprint", ""):
            additions.append(
                f'<static_index_status graph_id="{graph_id}" status="{index_status}">\n'
                f'files={coverage.get("files_indexed", 0)}/{coverage.get("files_total", 0)} '
                f'functions={coverage.get("functions", 0)} callsites={coverage.get("callsites", 0)}\n'
                '</static_index_status>'
            )
            state.injected_index_fingerprint = index_fp

        search = dict(getattr(state, "latest_sink_search_brief", {}) or {})
        search_fp = str(getattr(state, "sink_search_fingerprint", "") or "")
        if search and search_fp and search_fp != getattr(state, "injected_sink_search_fingerprint", ""):
            payload = str(search.get("context_payload") or "").strip()
            if payload:
                additions.append(payload)
            state.injected_sink_search_fingerprint = search_fp

        brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
        if brief:
            brief_fp = hashlib.sha256(json.dumps({
                "id": brief.get("brief_id"), "requirements": brief.get("requirements"),
                "paths": brief.get("candidate_paths"), "gaps": brief.get("gaps"),
            }, sort_keys=True, default=str).encode()).hexdigest()
            if brief_fp != getattr(state, "injected_brief_fingerprint", ""):
                payload = str(brief.get("context_payload") or "").strip()
                if payload:
                    additions.append(payload)
                state.injected_brief_fingerprint = brief_fp

        read_result = dict(getattr(state, "latest_read_analysis", {}) or {})
        read_fp = str(read_result.get("fingerprint") or "")
        if read_fp and read_fp != getattr(state, "injected_read_analysis_fingerprint", ""):
            if read_result.get("kind") == "static_analysis_delta":
                lines = [f'<static_analysis_delta path_id="{read_result.get("path_id", "")}">']
                for key in ("added", "revised", "invalidated"):
                    values = read_result.get(key) or []
                    if values:
                        lines.append(f"{key}:")
                        lines.extend(f"- {value}" for value in values[:3])
                lines.append("</static_analysis_delta>")
            else:
                focus = read_result.get("focus") or []
                lines = ["<code_index_context>"]
                if focus:
                    item = focus[0]
                    lines.append(f'focus={item.get("function")} @{item.get("file")}:{item.get("start_line")}-{item.get("end_line")}')
                lines.append(f'reachable_from_fuzz_entry={str(bool(read_result.get("reachable_from_entry"))).lower()}')
                if read_result.get("direct_callers"):
                    lines.append("callers=" + ", ".join(read_result["direct_callers"][:3]))
                callee_risks = read_result.get("callee_risks") or []
                if callee_risks:
                    callee_strs = []
                    has_risky = False
                    has_leaf = False
                    for cr in callee_risks[:6]:
                        name = cr.get("name", "?")
                        sub = cr.get("sub_callees", -1)
                        if sub == 0:
                            depth_tag = "(leaf)"
                            has_leaf = True
                        elif sub > 0:
                            depth_tag = f"({sub} callees)"
                        else:
                            depth_tag = ""
                        if cr.get("risk") == "risky":
                            callee_strs.append(f"⚠{name}{depth_tag}")
                            has_risky = True
                        else:
                            callee_strs.append(f"{name}{depth_tag}")
                    lines.append("callees=" + ", ".join(callee_strs))
                    if has_risky or has_leaf:
                        hint = "[HINT: ⚠ callees are common crash sites."
                        if has_leaf:
                            hint += " (leaf) callees have no sub-calls — they are the deepest functions and most likely the actual crash point."
                        else:
                            hint += " If the focus function is a sink candidate, check if a ⚠ callee is the actual leaf-level crash point."
                        hint += "]"
                        lines.append(hint)
                elif read_result.get("relevant_callees"):
                    lines.append("callees=" + ", ".join(read_result["relevant_callees"][:3]))
                for guard in (read_result.get("notable_guards") or [])[:3]:
                    lines.append(f"guard={guard}")
                for signal in (read_result.get("risk_signals") or [])[:3]:
                    lines.append(f'risk={signal.get("kind")}: {signal.get("expression", "")[:180]}')
                controlled = read_result.get("input_controlled_parameters") or {}
                if controlled:
                    lines.append("input_control=" + "; ".join(f"{name}({', '.join(values)})" for name, values in list(controlled.items())[:3]))
                if read_result.get("focus_role"):
                    lines.append("focus_role=" + str(read_result["focus_role"]))
                for lead in (read_result.get("navigation_leads") or [])[:2]:
                    next_read = lead.get("next_read") or {}
                    lines.append(f'next_read={lead.get("function")} @{next_read.get("path")} offset={next_read.get("offset")} limit={next_read.get("limit")} because={lead.get("why_inspect")}')
                lines.append("</code_index_context>")
            additions.append("\n".join(lines))
            state.injected_read_analysis_fingerprint = read_fp
        return prepared if not additions else prepared + "\n\n" + "\n\n".join(additions)
