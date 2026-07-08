"""Runtime synchronization for structured static-analysis bundles.

This module is intentionally state-first: every synced capability must land in
typed state or metadata, and every material update must bump a context revision
so the six-section observation can surface it on the next model turn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def sync_analysis_bundle_into_state(state: Any, bundle: dict[str, Any]) -> dict[str, Any]:
    """Synchronize a discovered static-analysis bundle into agent state.

    Returns a compact execution report with changed revision keys.  It does not
    claim success unless at least one known state surface was refreshed.
    """
    from ..core.runtime_context_contract import bump_context_revision

    if not isinstance(bundle, dict):
        return {"status": "blocked", "reason": "bundle is not a dict", "changed": []}

    changed: list[str] = []

    def bump(key: str) -> None:
        bump_context_revision(state, key)
        if key not in changed:
            changed.append(key)

    graphs = list(bundle.get("mechanism_graphs") or [])[:5]
    if graphs:
        state.crash_mechanism_graphs = graphs
        bump("mechanism_graphs")

    objectives = list(bundle.get("trigger_objectives") or [])[:8]
    if objectives:
        state.active_trigger_objectives = objectives
        bump("trigger_objectives")

    transcripts = list(bundle.get("protocol_transcript_plans") or [])[:4]
    if transcripts:
        state.protocol_transcript_plans = transcripts
        bump("protocol_transcripts")

    new_mappings = list(bundle.get("input_mappings") or [])
    if new_mappings:
        existing = list(getattr(state, "active_input_mappings", []) or [])
        existing_by_id = {m.get("mapping_id"): m for m in existing if isinstance(m, dict)}
        for mapping in new_mappings:
            if not isinstance(mapping, dict):
                continue
            mid = str(mapping.get("mapping_id") or "")
            if not mid:
                mid = f"mapping_{len(existing_by_id)}"
                mapping = {**mapping, "mapping_id": mid}
            old = existing_by_id.get(mid)
            if old is None:
                existing_by_id[mid] = mapping
                continue
            old_status = str(old.get("status") or "")
            new_status = str(mapping.get("status") or "")
            if new_status not in {"unresolved", "needs_field_localization"} or old_status in {"unresolved", "needs_field_localization"}:
                existing_by_id[mid] = {**old, **mapping}
        state.active_input_mappings = list(existing_by_id.values())[:8]
        bump("input_mappings")

    call_path_evidence = list(bundle.get("call_path_evidence") or [])[:16]
    if call_path_evidence:
        state.metadata["call_path_evidence"] = call_path_evidence
        _merge_call_path_evidence_into_objectives(state, call_path_evidence)
        bump("trigger_objectives")

    numeric_constraints = list(bundle.get("numeric_constraints") or [])[:16]
    if numeric_constraints:
        state.metadata["numeric_constraints"] = numeric_constraints
        bump("numeric_constraints")

    api_reachability = bundle.get("api_reachability")
    if api_reachability:
        state.metadata["api_reachability"] = api_reachability
        bump("harness_protocols")

    state._structured_analysis_bundle = dict(bundle)

    # Derived surfaces: harness protocol extensions, oracle assessments,
    # constraint solutions, and PoC recipe.
    try:
        from .transcript_runtime import fill_harness_consumption_extensions

        fill_harness_consumption_extensions(state)
    except Exception:
        pass

    try:
        from .oracle_runtime import sync_oracle_assessments

        oracle_report = sync_oracle_assessments(state)
        if oracle_report.get("changed"):
            bump("oracle_assessments")
            bump("trigger_objectives")
    except Exception:
        pass

    try:
        from .poc.recipe import compile_poc_recipe

        compile_poc_recipe(state)
        bump("poc_recipe")
    except Exception:
        pass

    return {
        "status": "executed" if changed else "blocked",
        "reason": "" if changed else "bundle had no syncable content",
        "changed": changed,
        "bundle_status": bundle.get("status", ""),
        "bundle_keys": sorted(str(key) for key in bundle.keys())[:12],
    }


def refresh_analysis_bundle(state: Any) -> dict[str, Any]:
    """Rebuild the static-analysis bundle when possible, otherwise resync an existing one.

    Returning ``blocked`` is preferable to a fake ``executed`` because feedback
    arbitration relies on the runner result to decide whether the agent has
    actually learned anything new.
    """
    ranked = list(getattr(state, "ranked_vulnerability_paths", []) or [])
    repo_dir = str(getattr(state, "repo_dir", "") or "")
    crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", {}) or {}).get("crash_type_prior", "") or "")

    if repo_dir and ranked and Path(repo_dir).is_dir():
        try:
            from ...analysis.service import AnalysisService

            service = AnalysisService(repo_dir, workspace_root=getattr(state, "workspace_root", "") or Path(repo_dir).parent)
            bundle = service.discover_structured_analysis_bundle(
                ranked_paths=ranked,
                description_analysis=None,
                harness=None,
                crash_type=crash_type,
                top_k=5,
            )
            if bundle.get("status") in {"success", "partial"}:
                return sync_analysis_bundle_into_state(state, bundle)
            return {
                "status": "blocked",
                "reason": f"analysis bundle status={bundle.get('status', 'unknown')}",
                "bundle_status": bundle.get("status", ""),
            }
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": f"analysis refresh failed: {type(exc).__name__}: {exc}",
            }

    existing = getattr(state, "_structured_analysis_bundle", None)
    if isinstance(existing, dict) and existing:
        report = sync_analysis_bundle_into_state(state, existing)
        if report.get("status") == "executed":
            report["reason"] = "resynced existing analysis bundle"
        return report

    missing = []
    if not repo_dir:
        missing.append("repo_dir")
    elif not Path(repo_dir).is_dir():
        missing.append("valid repo_dir")
    if not ranked:
        missing.append("ranked_vulnerability_paths")
    return {
        "status": "blocked",
        "reason": "missing " + " and ".join(missing or ["analysis bundle"]),
        "changed": [],
    }


def _merge_call_path_evidence_into_objectives(
    state: Any,
    call_path_evidence: list[dict[str, Any]],
) -> None:
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    if not objectives:
        return
    by_path: dict[str, list[dict[str, Any]]] = {}
    for evidence in call_path_evidence:
        if not isinstance(evidence, dict):
            continue
        path_id = str(evidence.get("path_id") or evidence.get("ranked_path_id") or "")
        if path_id:
            by_path.setdefault(path_id, []).append(evidence)
    for obj in objectives:
        if not isinstance(obj, dict):
            continue
        path_id = str(obj.get("ranked_path_id") or "")
        evidence = by_path.get(path_id, [])
        if evidence:
            existing = list(obj.get("call_path_evidence") or [])
            obj["call_path_evidence"] = (existing + evidence)[:6]
