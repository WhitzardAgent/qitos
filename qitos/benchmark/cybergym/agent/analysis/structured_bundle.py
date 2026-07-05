"""Structured analysis bundle service — extracted from service.py."""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


class StructuredBundleService:
    """Builds structured analysis bundles from ranked paths."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def discover_structured_analysis_bundle(
        self,
        *,
        ranked_paths: list[dict[str, Any]],
        description_analysis: dict[str, Any] | None = None,
        harness: dict[str, Any] | None = None,
        crash_type: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Build mechanism/objective/transcript/provenance summaries for active paths.

        This is the unified entry point for structured static analysis.
        Returns structured IR dicts ready for state synchronization.
        """
        from .mechanism_builder import build_mechanism_graphs
        from .trigger_objectives import build_trigger_objectives
        from .protocol_transcript import build_transcript_candidates
        from .provenance import infer_input_mappings_for_path

        if not ranked_paths:
            return {
                "status": "empty",
                "mechanism_graphs": [],
                "trigger_objectives": [],
                "protocol_transcript_plans": [],
                "input_mappings": [],
                "gaps": [],
            }

        try:
            # 1. Build mechanism graphs
            mechanism_graphs = build_mechanism_graphs(
                ranked_paths=ranked_paths,
                risk_signals_by_path=self._collect_risk_signals_by_path(),
                crash_type=crash_type,
                description_analysis=description_analysis,
                top_k=top_k,
            )

            # 2. Build trigger objectives
            trigger_objectives = build_trigger_objectives(
                ranked_paths=ranked_paths,
                mechanism_graphs=mechanism_graphs,
                input_mappings=[],  # Will be enriched in step 4
                crash_type=crash_type,
                description_analysis=description_analysis,
                top_k=top_k,
            )

            # 3. Build transcript candidates
            transcript_plans = build_transcript_candidates(
                ranked_paths=ranked_paths,
                mechanism_graphs=mechanism_graphs,
                harness=harness,
                crash_type=crash_type,
                description_analysis=description_analysis,
                top_k=3,
            )

            # 4. Build provenance / input mappings
            all_mappings: list[dict[str, Any]] = []
            for path in ranked_paths[:top_k]:
                path_id = path.get("path_id", "")
                graph = next((g for g in mechanism_graphs if g.get("ranked_path_id") == path_id), None)
                mappings = infer_input_mappings_for_path(
                    ranked_path=path,
                    mechanism_graph=graph,
                    harness=harness,
                    description_analysis=description_analysis,
                )
                all_mappings.extend(mappings)

            # 5. Collect gaps
            gaps = self._collect_structured_bundle_gaps(mechanism_graphs, trigger_objectives)

            # 5b. Fix B: AST query runtime + numeric constraints
            call_path_evidence: list[dict[str, Any]] = []
            numeric_constraints_list: list[dict[str, Any]] = []
            try:
                from .ast_query_runtime import ASTQueryRuntime
                from .numeric_constraints import extract_numeric_constraints

                repo_root = str(getattr(self._service, "repository", "") or "")
                if repo_root:
                    ast = ASTQueryRuntime(repo_root)
                    ast.ensure_indexed()

                    # Add call path evidence for each ranked path
                    for path in ranked_paths[:top_k]:
                        endpoint = path.get("endpoint", {}) or {}
                        target_fn = endpoint.get("function", "")
                        entry_fn = "LLVMFuzzerTestOneInput"
                        if target_fn:
                            paths_found = ast.call_paths(entry_fn, target_fn, max_depth=8)
                            if paths_found:
                                best = paths_found[0]
                                call_path_evidence.append({
                                    "path_id": path.get("path_id", ""),
                                    "call_path": best,
                                    "source": "ast_query_runtime",
                                })

                        # Add source_nodes and source_lines to mechanism graph
                        graph = next((g for g in mechanism_graphs if g.get("ranked_path_id") == path.get("path_id", "")), None)
                        if graph and target_fn:
                            xref_data = ast.xref(target_fn)
                            if xref_data.get("definitions"):
                                defn = xref_data["definitions"][0]
                                graph["source_nodes"] = [{
                                    "function": target_fn,
                                    "file": defn.get("file", ""),
                                    "line": defn.get("line", 0),
                                }]

                    # Extract numeric constraints from endpoint files
                    source_files = list({
                        e.get("file", "")
                        for path in ranked_paths[:top_k]
                        for e in [path.get("endpoint", {}) or {}]
                        if e.get("file")
                    })
                    if source_files and repo_root:
                        suspect_fns = [
                            path.get("endpoint", {}).get("function", "")
                            for path in ranked_paths[:top_k]
                        ]
                        numeric_constraints_list = extract_numeric_constraints(
                            source_files=source_files,
                            repo_root=repo_root,
                            suspect_functions=[f for f in suspect_fns if f],
                            crash_type=crash_type,
                        )

                    # Feed numeric constraints into trigger objectives
                    if numeric_constraints_list:
                        for obj in trigger_objectives:
                            pid = obj.get("ranked_path_id", "")
                            relevant = [nc for nc in numeric_constraints_list
                                       if any(s.get("file", "") in pid for s in nc.get("source", []))]
                            if not relevant:
                                relevant = numeric_constraints_list[:3]
                            obj["violation_formula"] = relevant[0].get("formula", obj.get("violation_formula", ""))
                            obj["input_fields"] = obj.get("input_fields", []) + relevant[0].get("input_fields", [])
                            obj["candidate_values"] = relevant[0].get("candidate_values", [])

            except Exception:
                pass

            # 6. Link objective_ids into mechanism graphs
            obj_by_path = {}
            for obj in trigger_objectives:
                pid = obj.get("ranked_path_id", "")
                obj_by_path.setdefault(pid, []).append(obj.get("objective_id", ""))
            for g in mechanism_graphs:
                pid = g.get("ranked_path_id", "")
                if pid in obj_by_path:
                    g["objective_ids"] = obj_by_path[pid]

            # 7. Link objective_ids into transcript plans
            for tr in transcript_plans:
                pid = tr.get("ranked_path_id", "")
                if pid in obj_by_path and obj_by_path[pid]:
                    tr["objective_id"] = obj_by_path[pid][0]

            return {
                "status": "success" if mechanism_graphs else "partial",
                "mechanism_graphs": mechanism_graphs,
                "trigger_objectives": trigger_objectives,
                "protocol_transcript_plans": transcript_plans,
                "input_mappings": all_mappings[:8],
                "call_path_evidence": call_path_evidence,
                "numeric_constraints": numeric_constraints_list,
                "gaps": gaps,
            }

        except Exception as exc:
            _LOG.error("discover_structured_analysis_bundle failed: %s: %s", type(exc).__name__, exc, exc_info=True)
            return {
                "status": "partial",
                "mechanism_graphs": [],
                "trigger_objectives": [],
                "protocol_transcript_plans": [],
                "input_mappings": [],
                "gaps": [{"reason": f"builder error: {type(exc).__name__}: {exc}"}],
            }

    def _collect_risk_signals_by_path(self) -> dict[str, list[dict[str, Any]]]:
        """Collect risk signals from indexed functions, grouped by path_id."""
        result: dict[str, list[dict[str, Any]]] = {}
        # This is a lightweight pass — collect from the index
        try:
            for fid, summary_data in self._service.store.scan("summary"):
                if not isinstance(summary_data, dict):
                    continue
                signals = summary_data.get("risk_signals") or []
                for sig in signals:
                    if not isinstance(sig, dict):
                        continue
                    # Assign to all paths that reference this function
                    # For now, collect generically
                    result.setdefault("_all", []).append(sig)
        except Exception:
            pass
        return result

    @staticmethod
    def _collect_structured_bundle_gaps(
        mechanism_graphs: list[dict[str, Any]],
        trigger_objectives: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collect gaps from mechanism graphs and objectives."""
        gaps: list[dict[str, Any]] = []
        for g in mechanism_graphs:
            for role in g.get("missing_roles", []):
                gaps.append({
                    "reason": f"missing {role} in mechanism {g.get('graph_id', '')}",
                    "ranked_path_id": g.get("ranked_path_id", ""),
                })
        for obj in trigger_objectives:
            for field in obj.get("input_fields", []):
                if field.get("status") == "needs_field_localization":
                    gaps.append({
                        "reason": f"unresolved field {field.get('field', '')} in objective {obj.get('objective_id', '')}",
                        "ranked_path_id": obj.get("ranked_path_id", ""),
                    })
        return gaps[:10]
