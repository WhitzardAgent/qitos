"""Feedback action runner — executes the required action from feedback arbitration.

Fix C: After derive_feedback_action() determines what the agent should do,
this module actually runs safe, read-only analysis actions and updates state.
Only read-only operations are allowed; the runner never directly submits a PoC.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState


def execute_feedback_action_if_safe(
    state: CyberGymState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Execute a feedback action if it can be done safely.

    Only read-only analysis and state/metadata generation are allowed.
    The runner never submits a PoC directly.

    Returns a dict with:
      status: "executed" | "blocked" | "skipped" | "failed"
      action: the action name that was attempted
      result: action-specific result data
      reason: why the action was blocked/skipped/failed (if applicable)
    """
    name = action.get("action", "")

    if name == "mine_local_tests":
        return _run_local_mining(state)
    if name == "extract_harness_protocol":
        return _run_harness_protocol_extraction(state)
    if name in {"localize_field", "switch_objective"}:
        return _run_static_bundle_refresh(state, reason=name)
    if name == "repair_carrier":
        return _refresh_recipe_and_consistency(state)
    if name == "repair_consistency":
        return _run_consistency_repair(state, action)
    if name == "verify_oracle_context":
        return _run_verify_oracle_context(state, action)

    # Unknown or submit_allowed — no automatic runner
    return {
        "status": "skipped",
        "action": name,
        "result": {},
        "reason": "no safe automatic runner for this action",
    }


def _run_local_mining(state: CyberGymState) -> dict[str, Any]:
    """Run local mining to find regression tests, corpus, and git history."""
    try:
        from ..repo.local_mining import mine_local_references
        from ..core.runtime_context_contract import bump_context_revision

        repo_root = str(getattr(state, "repo_dir", "") or "")
        if not repo_root:
            return {"status": "blocked", "action": "mine_local_tests", "result": {}, "reason": "no repo_dir"}

        vuln_desc = str(getattr(state, "vulnerability_description", "") or "")
        suspect_symbols = [
            str(c.function) for c in (state.confirmed_sink_candidates() or [])[:3]
        ]

        result = mine_local_references(
            repo_root=repo_root,
            vulnerability_description=vuln_desc,
            suspect_symbols=suspect_symbols,
        )

        if result.get("refs"):
            state.local_mining_refs = result["refs"]
            bump_context_revision(state, "local_mining_refs")
            return {
                "status": "executed",
                "action": "mine_local_tests",
                "result": {"n_refs": len(result["refs"]), "kinds": list({r.get("kind", "") for r in result["refs"]})},
                "reason": "",
            }
        else:
            return {
                "status": "executed",
                "action": "mine_local_tests",
                "result": {"n_refs": 0},
                "reason": "mining completed but found no references",
            }

    except Exception as e:
        return {
            "status": "failed",
            "action": "mine_local_tests",
            "result": {},
            "reason": f"mining failed: {e}",
        }


def _run_harness_protocol_extraction(state: CyberGymState) -> dict[str, Any]:
    """Run harness protocol extraction to determine input contract."""
    try:
        from ..poc.harness_protocol import extract_harness_protocol
        from ..core.runtime_context_contract import bump_context_revision

        # Get harness files from state
        harness_files = []
        harness_candidates = list(getattr(state, "harness_candidates", []) or [])
        for h in harness_candidates[:3]:
            if getattr(h, "source_path", ""):
                harness_files.append(h.source_path)

        source_root = str(getattr(state, "repo_dir", "") or "")
        if not harness_files or not source_root:
            return {
                "status": "blocked",
                "action": "extract_harness_protocol",
                "result": {},
                "reason": "no harness files or source_root available",
            }

        fuzzer_name = ""
        if harness_candidates:
            h = harness_candidates[0]
            fuzzer_name = str(getattr(h, "entry_function", "") or "")

        result = extract_harness_protocol(
            harness_files=harness_files,
            fuzzer_binary_name=fuzzer_name,
            source_root=source_root,
        )

        # Update state
        if result.get("input_contract") != "unknown":
            protocol = {
                "protocol_id": "hp_auto_1",
                **result,
            }
            # Don't overwrite existing protocols, add/merge
            existing = list(getattr(state, "harness_protocols", []) or [])
            existing_ids = {p.get("protocol_id") for p in existing}
            if "hp_auto_1" in existing_ids:
                existing = [p for p in existing if p.get("protocol_id") != "hp_auto_1"]
            existing.append(protocol)
            state.harness_protocols = existing
            bump_context_revision(state, "harness_protocols")

        return {
            "status": "executed",
            "action": "extract_harness_protocol",
            "result": {"input_contract": result.get("input_contract", "unknown")},
            "reason": "",
        }

    except Exception as e:
        return {
            "status": "failed",
            "action": "extract_harness_protocol",
            "result": {},
            "reason": f"extraction failed: {e}",
        }


def _run_static_bundle_refresh(
    state: CyberGymState,
    reason: str = "",
) -> dict[str, Any]:
    """Re-run static analysis bundle to refresh objectives/mappings.

    This is a lightweight refresh that re-runs the bundle sync
    without full re-initialization.
    """
    try:
        from ..runtime.bundle import refresh_analysis_bundle

        refresh = refresh_analysis_bundle(state)
        status = str(refresh.get("status") or "blocked")
        if status != "executed":
            return {
                "status": status,
                "action": reason,
                "result": refresh,
                "reason": str(refresh.get("reason") or "analysis bundle refresh did not execute"),
            }

        return {
            "status": "executed",
            "action": reason,
            "result": refresh,
            "reason": "",
        }

    except Exception as e:
        return {
            "status": "failed",
            "action": reason,
            "result": {},
            "reason": f"bundle refresh failed: {e}",
        }


def _refresh_recipe_and_consistency(state: CyberGymState) -> dict[str, Any]:
    """Refresh the PoC recipe and re-run consistency guard."""
    try:
        from ..poc.recipe import compile_poc_recipe
        from .consistency import evaluate_consistency
        from ..core.runtime_context_contract import bump_context_revision

        # Re-compile recipe
        recipe_result = compile_poc_recipe(state)

        # Re-evaluate consistency
        poc_path = ""
        if state.ready_pocs:
            poc_path = str(getattr(state.ready_pocs[0], "file_path", "") or "")

        consistency_result = evaluate_consistency(
            state=state,
            poc_path=poc_path or None,
            submit_result=None,
        )
        if consistency_result:
            state.consistency_signals = consistency_result
            bump_context_revision(state, "consistency_signals")

        bump_context_revision(state, "poc_recipe")

        return {
            "status": "executed",
            "action": "repair_carrier",
            "result": {
                "recipe_status": recipe_result.get("status", "unknown"),
                "n_consistency_signals": len(consistency_result) if consistency_result else 0,
            },
            "reason": "",
        }

    except Exception as e:
        return {
            "status": "failed",
            "action": "repair_carrier",
            "result": {},
            "reason": f"recipe/consistency refresh failed: {e}",
        }


def _run_consistency_repair(state: CyberGymState, action: dict[str, Any]) -> dict[str, Any]:
    """Attempt to repair consistency by re-running consistency guard and recipe."""
    try:
        from .consistency import evaluate_consistency
        from ..core.runtime_context_contract import bump_context_revision

        poc_path = ""
        if state.ready_pocs:
            poc_path = str(getattr(state.ready_pocs[0], "file_path", "") or "")

        consistency_result = evaluate_consistency(
            state=state,
            poc_path=poc_path or None,
            submit_result=None,
        )
        if consistency_result:
            state.consistency_signals = consistency_result
            bump_context_revision(state, "consistency_signals")

            # Check if blocks are still present
            blocks_remaining = [s for s in consistency_result if s.get("severity") == "block" or s.get("blocks_submit")]
            return {
                "status": "executed",
                "action": "repair_consistency",
                "result": {"blocks_remaining": len(blocks_remaining)},
                "reason": "",
            }
        else:
            return {
                "status": "executed",
                "action": "repair_consistency",
                "result": {"blocks_remaining": 0},
                "reason": "",
            }

    except Exception as e:
        return {
            "status": "failed",
            "action": "repair_consistency",
            "result": {},
            "reason": f"consistency repair failed: {e}",
        }


def _run_verify_oracle_context(
    state: CyberGymState,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Verify oracle/sanitizer context for the current objective.

    Checks whether the harness binary supports the required oracle
    (MSan, UBSan, ASan fake-stack, etc.) and whether the objective
    should be marked as not observable.

    This is a read-only analysis that updates the objective's
    no_trigger_diagnosis and may add negative evidence.
    """
    try:
        from ..core.runtime_context_contract import bump_context_revision

        objectives = list(getattr(state, "active_trigger_objectives", []) or [])
        target_ids = action.get("target_ids", {}) or {}
        target_oid = target_ids.get("objective_id", "")

        # Find the target objective
        target_obj = None
        for obj in objectives:
            if obj.get("objective_id") == target_oid:
                target_obj = obj
                break

        if not target_obj:
            return {
                "status": "blocked",
                "action": "verify_oracle_context",
                "result": {},
                "reason": f"objective {target_oid} not found",
            }

        oracle_kind = target_obj.get("oracle_kind", "")
        no_trigger_diag = target_obj.get("no_trigger_diagnosis", "")

        # Check harness binary vs oracle compatibility
        oracle_feasible = _check_oracle_feasibility(state, oracle_kind)

        result_info = {
            "oracle_kind": oracle_kind,
            "no_trigger_diagnosis": no_trigger_diag,
            "oracle_feasible": oracle_feasible,
        }

        if not oracle_feasible:
            # Oracle is not feasible — mark objective and add negative evidence
            target_obj["no_trigger_diagnosis"] = "oracle_not_observable"
            bump_context_revision(state, "trigger_objectives")

            # Add negative evidence
            state.append_negative_evidence(
                kind="objective_not_observable",
                candidate_id="",
                ranked_path_id=target_obj.get("ranked_path_id", ""),
                objective_id=target_oid,
                summary=f"Oracle {oracle_kind} not feasible in current harness; objective cannot produce expected signal",
                avoid_next="same_objective_without_oracle_switch",
            )

            return {
                "status": "executed",
                "action": "verify_oracle_context",
                "result": result_info,
                "reason": f"Oracle {oracle_kind} is not feasible; objective marked as not observable",
            }

        # Oracle is feasible — update diagnosis and suggest continuing
        if no_trigger_diag == "oracle_not_observable":
            target_obj["no_trigger_diagnosis"] = "trigger_condition_unmet"
            bump_context_revision(state, "trigger_objectives")

        return {
            "status": "executed",
            "action": "verify_oracle_context",
            "result": result_info,
            "reason": f"Oracle {oracle_kind} appears feasible; diagnosis updated to trigger_condition_unmet",
        }

    except Exception as e:
        return {
            "status": "failed",
            "action": "verify_oracle_context",
            "result": {},
            "reason": f"oracle verification failed: {e}",
        }


def _check_oracle_feasibility(state: CyberGymState, oracle_kind: str) -> bool:
    """Check whether the current harness supports the given oracle.

    Uses heuristic checks based on crash_type, harness protocols, and
    available binary information.
    """
    # Always feasible for standard ASan (most common)
    if oracle_kind == "asan":
        # Exception: stack-use-after-return requires detect_stack_use_after_return=1
        crash_type = str(getattr(state, "crash_type", "") or "")
        if "stack-use-after-return" in crash_type.lower():
            # Can't easily verify this; assume not feasible unless proven
            return False
        return True

    # MSan: check if the harness protocol or crash type suggests MSan binary
    if oracle_kind == "msan":
        # Heuristic: if crash_type contains "msan" or "uninitialized",
        # the binary likely supports MSan
        crash_type = str(getattr(state, "crash_type", "") or "")
        bug_type = str(getattr(state, "bug_type", "") or "")
        if "msan" in crash_type.lower() or "uninitial" in crash_type.lower():
            return True
        if "msan" in bug_type.lower():
            return True
        # Check harness protocols for MSan hints
        protocols = list(getattr(state, "harness_protocols", []) or [])
        for proto in protocols:
            sanity_checks = proto.get("sanity_checks", [])
            for sc in sanity_checks:
                if "msan" in str(sc.get("kind", "")).lower():
                    return True
        # Default: unknown — assume not feasible to avoid wasting time
        return False

    # UBSan: usually feasible if the crash_type mentions it
    if oracle_kind == "ubsan":
        return True

    # Leak: feasible with LSan (included in ASan)
    if oracle_kind == "leak":
        return True

    # Semantic/parser oracle: feasible by definition (no crash needed)
    if oracle_kind in ("semantic_accept", "parser_reach"):
        return True

    # Unknown oracle — assume feasible
    return True
