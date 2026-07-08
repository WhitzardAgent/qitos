"""Automatic Sink Enrichment integration for CyberGymAgent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from ...analysis.models import SinkCandidateInput, stable_value
from ...analysis.service import AnalysisService
from ..core.constants import CYBERGYM_STRUCTURED_ANALYSIS
from ..core.runtime_context_contract import bump_context_revision


def stable_description_analysis(state: Any) -> dict[str, Any] | None:
    analysis = getattr(state, "description_analysis", None)
    if analysis is None:
        return None
    return stable_value(analysis)


def stable_verified_refs(state: Any) -> list[dict[str, Any]]:
    return [
        stable_value(item)
        for item in list(getattr(state, "verified_search_refs", []) or [])
    ]


def stable_selected_harness(state: Any) -> dict[str, Any] | None:
    resolution = getattr(state, "harness_resolution", None)
    selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
    selected = next(
        (item for item in list(getattr(state, "harness_candidates", []) or [])
         if getattr(item, "candidate_id", "") == selected_id),
        None,
    )
    if selected is None:
        return None
    consumption = getattr(getattr(state, "input_format", None), "consumption", None)
    payload = stable_value(selected)
    if isinstance(payload, dict) and consumption is not None:
        payload["consumption"] = stable_value(consumption)
        payload["first_hops"] = list(getattr(consumption, "first_hops", []) or [])
    return payload


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
        try:
            report = service.index_repository(
                timeout_seconds=service.config.automatic_timeout_seconds,
                priority_files=[item for item in priorities if item],
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "index_repository failed: %s: %s", type(exc).__name__, exc, exc_info=True,
            )
            state.analysis_index_status = "INDEX_ERROR"
            return

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
        try:
            crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
            if CYBERGYM_STRUCTURED_ANALYSIS:
                ranked = service.discover_ranked_vulnerability_paths(
                    description_analysis=stable_description_analysis(state),
                    verified_refs=stable_verified_refs(state),
                    harness=stable_selected_harness(state),
                    crash_type=crash_type,
                    top_k=5,
                )
                self._sync_ranked_paths(state, ranked)
                # Structured analysis: build mechanism graphs, trigger objectives, etc.
                self._sync_structured_analysis_bundle(state, service, crash_type)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "discover_ranked_vulnerability_paths failed: %s: %s", type(exc).__name__, exc, exc_info=True,
            )
        try:
            crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
            search = service.discover_sink_navigation_leads(
                limit=5,
                description=str(getattr(state, "vulnerability_description", "") or ""),
                crash_type=crash_type,
            )
            self._sync_navigation_leads(state, search)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "discover_sink_navigation_leads failed: %s: %s", type(exc).__name__, exc, exc_info=True,
            )
        try:
            crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
            reachable = service.reachable_functions_from_entry(
                limit=20,
                crash_type=crash_type,
                description=str(getattr(state, "vulnerability_description", "") or ""),
            )
            if reachable.get("status") == "success":
                state.reachable_function_candidates = reachable.get("candidates", [])
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "reachable_functions_from_entry failed: %s: %s", type(exc).__name__, exc, exc_info=True,
            )

    @staticmethod
    def _sync_ranked_paths(state: Any, result: dict[str, Any]) -> None:
        if result.get("status") not in {"success", "partial"}:
            return
        from ...state import SinkCandidate

        paths = list(result.get("paths") or [])[:5]
        state.ranked_vulnerability_paths = paths
        state.ranked_paths_graph_id = str(result.get("graph_id") or "")
        state.ranked_paths_status = str(result.get("status") or "")
        existing_by_id = {item.candidate_id: item for item in list(getattr(state, "sink_candidates", []) or [])}
        current_ids: set[str] = set()
        roles = {str((path or {}).get("endpoint_role") or "") for path in paths}
        missing_reasons: list[str] = []
        if paths and "crash_site" not in roles:
            missing_reasons.append("no_crash_site_role")
        for path in paths:
            event_pair = (path or {}).get("event_pair") or {}
            if event_pair.get("missing_event"):
                missing_reasons.append("no_event_pair")
                break
        if missing_reasons:
            state.metadata["candidate_set_incomplete_reason"] = "|".join(sorted(set(missing_reasons)))
        else:
            state.metadata.pop("candidate_set_incomplete_reason", None)
        for path in paths:
            endpoint = path.get("endpoint") or {}
            candidate_id = "ranked_" + str(path.get("path_id") or "")
            current_ids.add(candidate_id)
            function = str(endpoint.get("function") or "").rsplit("::", 1)[-1]
            file_name = str(endpoint.get("file") or "")
            line = int(endpoint.get("line") or 0)
            role = str(path.get("endpoint_role") or "path_anchor")
            family = str(path.get("candidate_family") or "unknown")
            score = min(.49, max(.10, float(path.get("score") or 0.0)))
            evidence = (
                f"ranked vulnerability path {path.get('path_id')} role={role} "
                f"family={family} score={path.get('score')}"
            )
            metadata = {
                "requires_review": True,
                "reviewed": False,
                "selection_status": "unreviewed",
                "ranked_path_id": path.get("path_id"),
                "candidate_role": role,
                "candidate_family": family,
                "score_breakdown": path.get("score_breakdown", {}),
                "generation_channels": path.get("generation_channels", []),
                "next_read": path.get("next_read", {}),
                "normalization_warnings": path.get("normalization_warnings", []),
                "loop_detected": bool(path.get("loop_detected")),
            }
            candidate = existing_by_id.get(candidate_id)
            if candidate is None:
                state.sink_candidates.append(SinkCandidate(
                    function=function,
                    location=f"{file_name}:{line}" if file_name else "",
                    confidence=score,
                    evidence=evidence,
                    status="candidate",
                    source="static_navigation",
                    candidate_id=candidate_id,
                    file=file_name,
                    line=line,
                    category=family,
                    reason=evidence,
                    metadata=metadata,
                ))
            elif candidate.source == "static_navigation":
                candidate.confidence = score
                candidate.evidence = candidate.reason = evidence
                candidate.file = file_name
                candidate.line = line
                candidate.location = f"{file_name}:{line}" if file_name else ""
                candidate.category = family
                candidate.metadata = {**dict(candidate.metadata or {}), **metadata}
        state.sink_candidates = [
            item for item in state.sink_candidates
            if item.source != "static_navigation"
            or item.candidate_id in current_ids
            or not str(item.candidate_id).startswith("ranked_")
        ]
        bump_context_revision(state, "path")

    @staticmethod
    def _sync_navigation_leads(
        state: Any, result: dict[str, Any], *, allow_auto_promote: bool = True,
    ) -> None:
        """Keep Top-3 provisional leads in State without satisfying checkpoints."""
        if result.get("status") not in {"success", "partial"}:
            return
        from ...state import SinkCandidate

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
                # Properly eliminate stale candidates so they don't appear in
                # navigation_candidates() or the Likely section
                candidate.status = "eliminated"
                candidate.metadata["requires_review"] = False
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

        # ── Auto-promote: guarantee every trace has an active sink ──
        # If no confirmed sink exists yet, promote the top navigation lead
        # (skipping entry-point functions) so the formulation phase has a target.
        if allow_auto_promote and leads and not state.confirmed_sink_candidates():
            from ...analysis.vuln_patterns import is_entry_point_function
            for lead in leads:
                top_func = str(lead.get("function") or "").strip()
                top_score = float(lead.get("score") or 0)
                top_lead_id = str(lead.get("lead_id") or "")
                if not top_func or is_entry_point_function(top_func) or top_score < 0.3:
                    continue
                # Find the provisional candidate created above and promote it
                for candidate in state.sink_candidates:
                    if candidate.candidate_id == top_lead_id:
                        candidate.source = "model_candidate"
                        candidate.status = "candidate"
                        candidate.metadata = dict(candidate.metadata or {})
                        candidate.metadata["requires_review"] = False
                        candidate.metadata["reviewed"] = True
                        candidate.metadata["auto_promoted"] = True
                        candidate.metadata["confirmed_via"] = "auto_promotion"
                        break
                else:
                    continue  # Candidate not found, try next lead
                # Update active sink
                state.active_sink_id = state._primary_sink_id()
                state.active_sink_candidate_id = top_lead_id
                state.analysis_status = "TARGET_PROPOSED"
                state.metadata["_pending_sink_analysis"] = top_lead_id
                break  # Promoted one, done

    @staticmethod
    def _description_navigation_text(analysis: Any) -> str:
        fields = (
            "vuln_type", "access_mode", "memory_region", "mechanism_tags",
            "described_operations", "described_state_transitions", "numeric_facts",
            "suspect_functions", "suspect_files", "suspect_modules", "suspect_params",
            "trigger_conditions", "search_hints",
        )
        parts: list[str] = []
        for name in fields:
            value = getattr(analysis, name, "")
            if isinstance(value, list):
                parts.extend(str(item) for item in value[:12] if str(item).strip())
            elif str(value or "").strip():
                parts.append(str(value))
        return " ".join(parts)[:4000]

    def _refresh_description_analysis(self, state: Any) -> None:
        """Verify newly recorded description priors and refresh navigation once."""
        metadata = getattr(state, "metadata", {}) or {}
        if not metadata.get("_description_analysis_dirty"):
            return
        service = self._analysis_service(state)
        if service is None:
            metadata["_description_analysis_refresh_error"] = "analysis service unavailable"
            return
        analysis = getattr(state, "description_analysis", None)
        try:
            result = service.verify_description_references(analysis, limit_per_hint=5)
        except Exception as exc:
            metadata["_description_analysis_refresh_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            return

        from ...state import VerifiedCodeRef

        step = int(getattr(state, "current_step", 0) or 0)
        prior_created = {
            str(getattr(item, "ref_id", "") or ""): int(getattr(item, "created_step", step) or step)
            for item in list(getattr(state, "verified_search_refs", []) or [])
        }
        refs: list[VerifiedCodeRef] = []
        for item in list(result.get("refs") or [])[:24]:
            values = dict(item)
            ref_id = str(values.get("ref_id") or "")
            values["created_step"] = prior_created.get(ref_id, step)
            values["last_relevant_step"] = step
            refs.append(VerifiedCodeRef(**values))
        state.verified_search_refs = refs
        state.unresolved_search_hints = [
            str(item) for item in list(result.get("unresolved_hints") or [])[:24]
        ]
        analysis.status = "partial" if result.get("status") == "partial" else "verified"
        analysis.last_relevant_step = step
        metadata.pop("_description_analysis_dirty", None)
        metadata.pop("_description_analysis_refresh_error", None)
        metadata["_description_reference_gaps"] = list(result.get("gaps") or [])[:4]
        bump_context_revision(state, "description")

        description = self._description_navigation_text(analysis)
        focus_ids = [item.symbol_id for item in refs if item.symbol_id]
        crash_type = str(getattr(analysis, "crash_type_hint", "") or metadata.get("crash_type_prior", ""))
        try:
            search = service.discover_sink_navigation_leads(
                limit=5, description=description,
                focus_symbol_ids=focus_ids, crash_type=crash_type,
            )
            self._sync_navigation_leads(state, search, allow_auto_promote=False)
        except Exception as exc:
            metadata["_description_navigation_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        try:
            if CYBERGYM_STRUCTURED_ANALYSIS:
                ranked = service.discover_ranked_vulnerability_paths(
                    description_analysis=stable_description_analysis(state),
                    verified_refs=stable_verified_refs(state),
                    harness=stable_selected_harness(state),
                    crash_type=crash_type,
                    top_k=5,
                )
                self._sync_ranked_paths(state, ranked)
        except Exception as exc:
            metadata["_description_ranked_path_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        try:
            reachable = service.reachable_functions_from_entry(
                limit=20, crash_type=crash_type, description=description,
            )
            if reachable.get("status") == "success":
                state.reachable_function_candidates = reachable.get("candidates", [])
        except Exception as exc:
            metadata["_description_reachability_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"

    @staticmethod
    def _sync_structured_analysis_bundle(state: Any, service: Any, crash_type: str) -> None:
        """Build and sync structured analysis bundle (mechanism graphs, trigger objectives, etc.)."""
        try:
            bundle = service.discover_structured_analysis_bundle(
                ranked_paths=list(getattr(state, "ranked_vulnerability_paths", []) or []),
                description_analysis=stable_description_analysis(state),
                harness=stable_selected_harness(state),
                crash_type=crash_type,
                top_k=5,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "discover_structured_analysis_bundle failed: %s: %s", type(exc).__name__, exc, exc_info=True,
            )
            return

        if bundle.get("status") not in ("success", "partial"):
            return

        try:
            from ...analysis.api_reachability import analyze_api_reachability

            harness_files = [
                str(getattr(item, "source_path", "") or "")
                for item in list(getattr(state, "harness_candidates", []) or [])
                if str(getattr(item, "source_path", "") or "")
            ][:4]
            if harness_files and "api_reachability" not in bundle:
                bundle["api_reachability"] = analyze_api_reachability(
                    harness_files=harness_files,
                    source_root=str(getattr(state, "repo_dir", "") or ""),
                    harness_protocols=list(getattr(state, "harness_protocols", []) or []),
                )
        except Exception:
            pass

        try:
            from .analysis_bundle_runtime import sync_analysis_bundle_into_state
            sync_analysis_bundle_into_state(state, bundle)
        except Exception:
            return

        # Run local mining if no refs yet
        try:
            existing_refs = list(getattr(state, "local_mining_refs", []) or [])
            if not existing_refs:
                from .local_mining import mine_local_references
                desc = str(getattr(state, "vulnerability_description", "") or "")
                symbols = list(getattr(state, "symbols_mentioned", []) or [])
                repo_dir = str(getattr(state, "repo_dir", "") or "")
                if repo_dir:
                    mining_result = mine_local_references(
                        repo_root=repo_dir,
                        vulnerability_description=desc,
                        suspect_symbols=symbols,
                        project_name=str(getattr(state, "task_id", "") or ""),
                        limit=10,
                    )
                    if mining_result.get("refs"):
                        state.local_mining_refs = mining_result["refs"][:10]
                        from ..core.runtime_context_contract import bump_context_revision
                        bump_context_revision(state, "local_mining_refs")
        except Exception:
            pass

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
        crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
        navigation = service.discover_sink_navigation_leads(
            limit=5,
            description=str(getattr(state, "vulnerability_description", "") or ""),
            focus_symbol_ids=[str(item.get("symbol_id") or "") for item in result.get("focus", [])],
            crash_type=crash_type,
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
            state.active_input_mappings = list(state.latest_sink_analysis_brief.get("input_mappings") or [])[:8]
            if state.active_input_mappings:
                bump_context_revision(state, "mapping")
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
                    }],
                    "unresolved": [],
                }

    @staticmethod
    def _populate_chain_nodes_from_brief(state: Any) -> None:
        """Auto-populate ChainNode entries from the SinkAnalysisBrief's discovered paths."""
        from ...state import ChainNode

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
                from ...state import SinkCandidate
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
        mappings = list(brief.get("input_mappings") or [])
        state.active_input_mappings = mappings[:8]
        if not requirements and not mappings:
            return
        existing_formulas = {
            (s.get("normalized_formula", ""), s.get("path_id", ""))
            for s in (getattr(state, "suggested_constraints", []) or [])
        }
        suggestions = list(getattr(state, "suggested_constraints", []) or [])
        for mapping in mappings[:6]:
            if mapping.get("status") == "unresolved":
                continue
            mapping_id = str(mapping.get("mapping_id") or "")
            expr = f"{mapping.get('sink_argument')} <- input[{mapping.get('offset_expression') or '?'}]"
            path_id = str(getattr(state, "selected_analysis_path_id", "") or "")
            dedup_key = (expr, path_id)
            if dedup_key in existing_formulas:
                continue
            suggestions.append({
                "requirement_id": "req_" + mapping_id,
                "gate_type": "value_gate",
                "description": f"Input mapping: {expr}",
                "required_condition": expr,
                "normalized_formula": expr,
                "safe_formula": "",
                "violation_formula": "",
                "input_mapping": mapping_id,
                "polarity": "satisfy",
                "origin": "input_mapping",
                "source_span": (mapping.get("evidence") or [{}])[0] if mapping.get("evidence") else {},
                "node_function": "",
                "role": "dataflow",
                "path_id": path_id,
                "confidence": "high" if float(mapping.get("confidence", 0) or 0) >= .8 else "medium",
                "analysis_status": mapping.get("status", "inferred"),
            })
            existing_formulas.add(dedup_key)
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

        # --- Sync recipe from brief ---
        # Build poc_recipe from mappings and requirements
        if hasattr(state, "get_poc_recipe"):
            recipe = state.get_poc_recipe()
        else:
            recipe = (getattr(state, "metadata", {}) or {}).get("poc_recipe", {})
            if not isinstance(recipe, dict):
                recipe = {}
        # Set sink/path linkage
        primary_sink = str(getattr(state, "active_sink_candidate_id", "") or "")
        path_id = str(getattr(state, "selected_analysis_path_id", "") or "")
        if primary_sink:
            recipe.setdefault("sink_candidate_id", primary_sink)
        if path_id:
            recipe.setdefault("ranked_path_id", path_id)

        # Add confirmed/inferred mappings as trigger mutations
        for mapping in mappings[:4]:
            status = str(mapping.get("status", "unresolved"))
            if status in ("unresolved", "refuted"):
                continue
            mutation = {
                "mapping_id": str(mapping.get("mapping_id", "")),
                "argument_role": str(mapping.get("argument_role", "")),
                "offset": mapping.get("offset"),
                "width": mapping.get("width"),
                "endianness": str(mapping.get("endianness", "unknown")),
                "value_strategy": str(mapping.get("value_strategy", "")),
                "constraint": str(mapping.get("constraint", "")),
                "evidence": str(
                    (mapping.get("evidence") or [{}])[0].get("file", "")
                    if mapping.get("evidence") else ""
                ),
            }
            if hasattr(state, "add_trigger_mutation"):
                state.add_trigger_mutation(mutation)
            else:
                existing = recipe.setdefault("trigger_mutations", [])
                mid = mutation["mapping_id"]
                if mid:
                    existing[:] = [m for m in existing if m.get("mapping_id") != mid]
                existing.append(mutation)
                recipe["trigger_mutations"] = existing[-6:]

        # Add trigger requirements as gaps if they have no input mapping
        for c in requirements[:6]:
            role = str(c.get("role", ""))
            if role != "trigger":
                continue
            im = str(c.get("input_mapping", ""))
            if not im:
                expr = str(c.get("expression", "")).strip()
                if expr:
                    gap = f"Trigger: {expr} — no input mapping"
                    if hasattr(state, "add_recipe_gap"):
                        state.add_recipe_gap(gap)
                    else:
                        gaps = recipe.setdefault("open_gaps", [])
                        if gap not in gaps:
                            gaps.append(gap)
                            recipe["open_gaps"] = gaps[:8]

        # Store recipe
        if hasattr(state, "metadata") and isinstance(state.metadata, dict):
            state.metadata["poc_recipe"] = recipe

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
        """Inject static analysis data into state for section renderers.

        Instead of appending orphaned Markdown sections after the observation brief,
        this method writes analysis data into state.metadata so that the section
        renderers (Current Assessment, Required Conditions, Vulnerability
        Path, Next Action) can consume it. Nothing is appended as an orphan
        XML/Markdown block.
        """
        meta = getattr(state, "metadata", {}) or {}

        # 1. Static index status — metadata only, never model-facing XML.
        graph_id = str(getattr(state, "analysis_graph_id", "") or "")
        index_status = str(getattr(state, "analysis_index_status", "NO_INDEX") or "NO_INDEX")
        coverage = dict(getattr(state, "analysis_index_coverage", {}) or {})
        index_fp = hashlib.sha256(json.dumps({"graph": graph_id, "status": index_status, "coverage": coverage}, sort_keys=True).encode()).hexdigest()
        if graph_id and index_fp != getattr(state, "injected_index_fingerprint", ""):
            meta["_static_index_summary"] = {
                "graph_id": graph_id,
                "status": index_status,
                "files_indexed": coverage.get("files_indexed", 0),
                "files_total": coverage.get("files_total", 0),
                "functions": coverage.get("functions", 0),
                "callsites": coverage.get("callsites", 0),
            }
            state.injected_index_fingerprint = index_fp

        # 2. Sink search brief — compatibility fingerprint only. Ranked paths
        #    and provisional SinkCandidate records are the model-facing source.
        search = dict(getattr(state, "latest_sink_search_brief", {}) or {})
        search_fp = str(getattr(state, "sink_search_fingerprint", "") or "")
        if search and search_fp and search_fp != getattr(state, "injected_sink_search_fingerprint", ""):
            state.injected_sink_search_fingerprint = search_fp

        # 3. Analysis brief — store rendered sections in state.metadata for
        #    Required Conditions and Vulnerability Path to consume.
        brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
        if brief:
            brief_fp = hashlib.sha256(json.dumps({
                "id": brief.get("brief_id"), "requirements": brief.get("requirements"),
                "paths": brief.get("candidate_paths"), "gaps": brief.get("gaps"),
            }, sort_keys=True, default=str).encode()).hexdigest()
            if brief_fp != getattr(state, "injected_brief_fingerprint", ""):
                from ...analysis.ir_renderer import IRRenderer
                sections = IRRenderer.render_brief_sections(brief)
                if sections:
                    meta["_analysis_brief_sections"] = sections
                state.injected_brief_fingerprint = brief_fp

        # 4. Code index context — store in state.metadata for Next Action
        #    to reference when generating recommended READ targets.
        read_result = dict(getattr(state, "latest_read_analysis", {}) or {})
        read_fp = str(read_result.get("fingerprint") or "")
        if read_fp and read_fp != getattr(state, "injected_read_analysis_fingerprint", ""):
            # Store compact code context for section renderers
            focus = read_result.get("focus") or []
            ctx_lines = []
            if focus:
                item = focus[0]
                ctx_lines.append(f"- Focus: `{item.get('function')}` @{item.get('file')}:{item.get('start_line')}-{item.get('end_line')}")
            ctx_lines.append(f"- Reachable from fuzz entry: {str(bool(read_result.get('reachable_from_entry'))).lower()}")
            callee_risks = read_result.get("callee_risks") or []
            if callee_risks:
                callee_parts = []
                for cr in callee_risks[:6]:
                    name = cr.get("name", "?")
                    sub = cr.get("sub_callees", -1)
                    tag = "(leaf)" if sub == 0 else f"({sub} callees)" if sub > 0 else ""
                    risk_tag = " [RISKY]" if cr.get("risk") == "risky" else ""
                    callee_parts.append(f"`{name}`{tag}{risk_tag}")
                ctx_lines.append(f"- Callees: {', '.join(callee_parts)}")
            elif read_result.get("relevant_callees"):
                ctx_lines.append(f"- Callees: {', '.join(read_result['relevant_callees'][:3])}")
            for guard in (read_result.get("notable_guards") or [])[:3]:
                ctx_lines.append(f"- Guard: {guard}")
            for signal in (read_result.get("risk_signals") or [])[:3]:
                ctx_lines.append(f"- Risk: {signal.get('kind')}: {signal.get('expression', '')[:120]}")
            controlled = read_result.get("input_controlled_parameters") or {}
            if controlled:
                controlled_parts = []
                for name, values in list(controlled.items())[:3]:
                    controlled_parts.append(f"{name}({', '.join(values)})")
                ctx_lines.append(f"- Input controlled: {'; '.join(controlled_parts)}")
            if read_result.get("focus_role"):
                ctx_lines.append(f"- Role: {read_result['focus_role']}")
            # Store navigation leads from read analysis for Next Action
            nav_leads = []
            for lead in (read_result.get("navigation_leads") or [])[:2]:
                next_read = lead.get("next_read") or {}
                nav_leads.append({
                    "function": lead.get("function", ""),
                    "path": next_read.get("path", ""),
                    "offset": next_read.get("offset", ""),
                    "limit": next_read.get("limit", ""),
                    "why": lead.get("why_inspect", ""),
                })
            if ctx_lines:
                meta["_code_context_markdown"] = "\n".join(ctx_lines)
            if nav_leads:
                meta["_code_context_nav_leads"] = nav_leads
            state.injected_read_analysis_fingerprint = read_fp

        return prepared
