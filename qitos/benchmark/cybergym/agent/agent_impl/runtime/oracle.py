"""Runtime synchronization for oracle assessments."""

from __future__ import annotations

from typing import Any


def sync_oracle_assessments(state: Any) -> dict[str, Any]:
    from ...analysis.oracle_classifier import classify_oracle
    from ..core.runtime_context_contract import bump_context_revision

    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    graphs = list(getattr(state, "crash_mechanism_graphs", []) or [])
    ranked = list(getattr(state, "ranked_vulnerability_paths", []) or [])
    harness = _selected_harness_payload(state)
    assessments: list[dict[str, Any]] = []
    changed = False

    for obj in objectives:
        if not isinstance(obj, dict):
            continue
        path_id = str(obj.get("ranked_path_id") or "")
        graph = next((g for g in graphs if str(g.get("ranked_path_id") or "") == path_id), None)
        path = next((p for p in ranked if str(p.get("path_id") or "") == path_id), None)
        assessment = classify_oracle(
            crash_type=str(getattr(state, "crash_type", "") or ""),
            vulnerability_description=str(getattr(state, "vulnerability_description", "") or ""),
            mechanism_graph=graph,
            ranked_path=path,
            harness=harness,
        )
        assessment["objective_id"] = str(obj.get("objective_id") or "")
        assessment["ranked_path_id"] = path_id
        assessments.append(assessment)
        for key in ("oracle_kind", "oracle_signal", "observable_by_submit"):
            if obj.get(key) != assessment.get(key):
                obj[key] = assessment.get(key)
                changed = True
        if not obj.get("no_trigger_diagnosis"):
            obj["no_trigger_diagnosis"] = (
                "oracle_not_observable"
                if assessment.get("observable_by_submit") is False
                else "trigger_condition_unmet"
            )
            changed = True
        obj["observability_reason"] = assessment.get("observability_reason", "")

    if assessments:
        state.metadata["oracle_assessments"] = assessments[:8]
        bump_context_revision(state, "oracle_assessments")
        if changed:
            bump_context_revision(state, "trigger_objectives")

    return {"status": "executed" if assessments else "blocked", "changed": changed or bool(assessments), "assessments": assessments[:8]}


def _selected_harness_payload(state: Any) -> dict[str, Any]:
    resolution = getattr(state, "harness_resolution", None)
    selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
    for item in list(getattr(state, "harness_candidates", []) or []):
        if selected_id and getattr(item, "candidate_id", "") != selected_id:
            continue
        return {
            "source_path": str(getattr(item, "source_path", "") or ""),
            "entry_function": str(getattr(item, "entry_function", "") or ""),
            "binary_path": str(getattr(item, "binary_path", "") or ""),
        }
    return {}
