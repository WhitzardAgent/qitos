"""Feedback arbitration — converts raw submit feedback into a required
next planning action that enters the Next Action priority chain.

This is the key integration layer that makes structured runtime signals change
agent behavior. The arbitration result is stored in
state.metadata["last_feedback_action"] and read by
derive_contract_next_action_block() to produce hard blocks in Next Action.

Priority order (highest = most blocking):
1. Carrier sanity fail -> repair_carrier
2. Consistency block -> repair_consistency
3. Transcript gap -> complete_transcript
4. Unresolved field -> localize_field
5. Same objective repeated miss -> switch_objective
6. Same path repeated miss -> retire_candidate / change_seed
7. Otherwise -> submit_allowed (soft action may still be suggested)
"""

from __future__ import annotations

import hashlib
from typing import Any, TYPE_CHECKING

from ..core.metadata_keys import (
    FRONTIER_PROBES,
    INVOCATION_PROFILE,
    RUNTIME_EVIDENCE,
    STAGED_BINARY_CAPABILITY,
)

if TYPE_CHECKING:
    from ...state import CyberGymState


def derive_feedback_action(
    *,
    state: CyberGymState,
    submit_result: dict[str, Any] | None = None,
    failed_gate: str = "",
) -> dict[str, Any]:
    """Convert raw submit feedback into a required next planning action.

    Returns a dict with:
      action: one of the action types below
      reason: why this action is required
      negative_evidence_kind: kind to record (empty if none)
      blocks_submit: whether this blocks further submissions
      target_ids: dict of scoped IDs (objective_id, transcript_id, etc.)
      prompt_instruction: instruction for the model
    """
    if failed_gate in {
        "no_crash_unknown",
        "path_not_reached",
        "trigger_wrong_signature",
        "trigger_wrong_location",
    }:
        _ensure_transient_objective_for_feedback(
            state,
            _latest_feedback_candidate_path(state),
        )

    # 1. Carrier sanity fail
    last_sanity = (state.metadata or {}).get("last_poc_sanity") or {}
    if isinstance(last_sanity, dict) and not last_sanity.get("passed", True):
        issues = last_sanity.get("issues", [])
        repair = ""
        for issue in issues:
            if isinstance(issue, dict) and issue.get("repair_hint"):
                repair = issue["repair_hint"]
                break
        return {
            "action": "repair_carrier",
            "reason": f"PoC failed carrier sanity: {issues[0].get('summary', '')[:120]}" if issues else "PoC carrier is invalid",
            "negative_evidence_kind": "carrier_sanity_fail",
            "blocks_submit": True,
            "target_ids": {},
            "prompt_instruction": f"Fix carrier structure before submitting. {repair}",
        }

    # 2. Consistency block
    signals = list(getattr(state, "consistency_signals", []) or [])
    blocks = [s for s in signals if s.get("severity") == "block" or s.get("blocks_submit")]
    if blocks:
        sig = blocks[0]
        return {
            "action": "repair_consistency",
            "reason": sig.get("summary", "Consistency block")[:200],
            "negative_evidence_kind": "consistency_block",
            "blocks_submit": True,
            "target_ids": {
                "consistency_signal_id": sig.get("signal_id", ""),
                "ranked_path_id": _best_ranked_path_id(state),
            },
            "prompt_instruction": f"Resolve consistency block: {sig.get('repair_action', '')[:200]}",
        }

    # 2b. Oracle classifier hard block: do not keep blind-submitting an
    # objective that the submit oracle cannot observe.
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    for obj in objectives:
        if obj.get("status") != "active":
            continue
        if obj.get("observable_by_submit") is False or obj.get("no_trigger_diagnosis") == "oracle_not_observable":
            oid = obj.get("objective_id", "")
            return {
                "action": "verify_oracle_context",
                "reason": obj.get("observability_reason", "") or f"Objective {oid} is not observable by submit_poc",
                "negative_evidence_kind": "objective_not_observable",
                "blocks_submit": True,
                "target_ids": {"objective_id": oid, "ranked_path_id": obj.get("ranked_path_id", "")},
                "prompt_instruction": "Verify harness/oracle observability or switch objective before submitting more variants.",
            }

    # 2c. Frontier probe: use the latest observed boundary to choose the next
    # analysis action instead of falling back to generic submit.
    latest_frontier = _latest_frontier_probe(state)
    if latest_frontier:
        status = str(latest_frontier.get("status") or "")
        normalized_status = _normalize_frontier_status(status)
        recommended = str(latest_frontier.get("recommended_action") or "")
        if normalized_status in {"wrong_harness", "path_not_reached", "trigger_unmet", "oracle_not_observable", "frontier_unknown"} and recommended:
            return {
                "action": recommended,
                "reason": str(latest_frontier.get("reason") or f"frontier={status}")[:200],
                "negative_evidence_kind": _frontier_negative_kind(normalized_status),
                "blocks_submit": normalized_status in {"wrong_harness", "path_not_reached", "oracle_not_observable"},
                "target_ids": {"ranked_path_id": _best_ranked_path_id(state)},
                "prompt_instruction": f"Resolve latest frontier probe status={status} frontier={latest_frontier.get('frontier', '')}.",
            }

    # 3. Transcript gap
    fmt = getattr(state, "input_format", None)
    consumption = getattr(fmt, "consumption", None) if fmt else None
    transcript_required = getattr(consumption, "transcript_required", False) if consumption else False
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    active_tr = [t for t in transcripts if t.get("status") in ("active", "candidate")]

    if transcript_required or active_tr:
        from ..runtime.transcript import transcript_gap_for_current_recipe
        gap = transcript_gap_for_current_recipe(state)
        if gap.get("wrong_scope") or gap.get("missing_steps"):
            tr_id = active_tr[0].get("transcript_id", "") if active_tr else ""
            return {
                "action": "complete_transcript",
                "reason": gap.get("summary", "Transcript plan has gaps")[:200],
                "negative_evidence_kind": "transcript_endpoint_mismatch",
                "blocks_submit": True,
                "target_ids": {
                    "transcript_id": tr_id,
                    "ranked_path_id": _best_ranked_path_id(state),
                },
                "prompt_instruction": "Complete transcript steps before submitting; do not submit single-buffer PoC.",
            }

    # 4. Unresolved input field (localize_field)
    for obj in objectives:
        if obj.get("status") != "active":
            continue
        input_fields = obj.get("input_fields", [])
        unresolved = [f for f in input_fields if f.get("status") == "needs_field_localization"]
        if unresolved:
            oid = obj.get("objective_id", "")
            return {
                "action": "localize_field",
                "reason": f"Objective {oid} has {len(unresolved)} unresolved input field(s)",
                "negative_evidence_kind": "objective_not_satisfied",
                "blocks_submit": True,
                "target_ids": {
                    "objective_id": oid,
                    "ranked_path_id": obj.get("ranked_path_id", ""),
                },
                "prompt_instruction": f"Localize field offsets for {', '.join(f.get('field', '?') for f in unresolved[:3])} before submitting.",
            }

    # 4b. Dynamic diagnosis after an oracle miss.  This is intentionally after
    # known static blockers, but before switching objectives or changing seeds:
    # if the staged binary is available, first classify whether the candidate
    # reaches the harness/parser/sink instead of guessing from submit feedback.
    runtime_action = _runtime_diagnosis_action_if_applicable(state, failed_gate)
    if runtime_action:
        return runtime_action

    # 5. Same objective repeated miss (2+ no-crash for same objective)
    #    Oracle-aware: different actions depending on oracle_kind and diagnosis
    if failed_gate in ("no_crash_unknown", "trigger_wrong_signature", "trigger_wrong_location"):
        for obj in objectives:
            if obj.get("status") != "active":
                continue
            oid = obj.get("objective_id", "")
            if oid:
                ne_for_obj = state.recent_negative_evidence_for_scope(objective_id=oid)
                obj_misses = [e for e in ne_for_obj if e.get("kind") == "objective_not_satisfied"]

                # Update no_trigger_diagnosis based on actual feedback
                from ...analysis.trigger_objectives import update_no_trigger_diagnosis
                update_no_trigger_diagnosis(
                    objective=obj,
                    failed_gate=failed_gate,
                    miss_count=len(obj_misses),
                )

                if len(obj_misses) >= 2:
                    oracle_kind = obj.get("oracle_kind", "")
                    no_trigger_diag = obj.get("no_trigger_diagnosis", "")

                    # Oracle-aware rule 1: MSan + repeated miss → verify oracle context
                    if oracle_kind == "msan" or no_trigger_diag == "oracle_not_observable":
                        return {
                            "action": "verify_oracle_context",
                            "reason": (
                                f"Objective {oid} has {len(obj_misses)} misses with oracle={oracle_kind}; "
                                f"diagnosis={no_trigger_diag}. Verify the harness binary supports "
                                f"this oracle before continuing."
                            ),
                            "negative_evidence_kind": "objective_not_observable",
                            "blocks_submit": True,
                            "target_ids": {
                                "objective_id": oid,
                                "ranked_path_id": obj.get("ranked_path_id", ""),
                            },
                            "prompt_instruction": (
                                f"Oracle {oracle_kind} may not be observable in this harness. "
                                f"Verify: (1) the harness binary is built with {oracle_kind} instrumentation, "
                                f"(2) the trigger path is actually reachable, "
                                f"(3) switch_objective if oracle is not supported."
                            ),
                        }

                    # Oracle-aware rule 2: semantic_accept/parser_reach — don't require crash
                    if oracle_kind in ("semantic_accept", "parser_reach"):
                        return {
                            "action": "verify_oracle_context",
                            "reason": (
                                f"Objective {oid} oracle={oracle_kind} does not require a crash. "
                                f"Provide reachability proof or expected output instead."
                            ),
                            "negative_evidence_kind": "objective_not_observable",
                            "blocks_submit": True,
                            "target_ids": {
                                "objective_id": oid,
                                "ranked_path_id": obj.get("ranked_path_id", ""),
                            },
                            "prompt_instruction": (
                                f"This oracle ({oracle_kind}) does not produce a crash signal. "
                                f"Instead: prove the target code path is reached, or verify the "
                                f"expected stderr/stdout output. Do not submit more crash variants."
                            ),
                        }

                    # Oracle-aware rule 3: wrong_harness → different action
                    if no_trigger_diag == "wrong_harness":
                        return {
                            "action": "extract_harness_protocol",
                            "reason": f"Objective {oid} diagnosis=wrong_harness; re-extract harness contract",
                            "negative_evidence_kind": "wrong_harness_binary",
                            "blocks_submit": True,
                            "target_ids": {
                                "objective_id": oid,
                                "ranked_path_id": obj.get("ranked_path_id", ""),
                            },
                            "prompt_instruction": (
                                "The harness binary or input protocol may be wrong. "
                                "Re-read the harness entry function and verify the fuzzer binary matches."
                            ),
                        }

                    # Default: switch_objective
                    return {
                        "action": "switch_objective",
                        "reason": f"Objective {oid} has {len(obj_misses)} misses; switch or replan",
                        "negative_evidence_kind": "objective_not_satisfied",
                        "blocks_submit": True,
                        "target_ids": {
                            "objective_id": oid,
                            "ranked_path_id": obj.get("ranked_path_id", ""),
                        },
                        "prompt_instruction": "Switch to a different objective or replan the current one; do not resubmit for the same objective.",
                    }

    # 6. Same ranked path repeated miss (3+ no-crash)
    if failed_gate in ("no_crash_unknown",):
        ranked_path_id = _best_ranked_path_id(state)
        if ranked_path_id:
            ne_list: list[dict[str, Any]] = (state.metadata or {}).get("negative_evidence", [])
            if isinstance(ne_list, list):
                same_path = [e for e in ne_list
                             if e.get("ranked_path_id") == ranked_path_id
                             and e.get("kind") in ("no_crash_unknown", "path_reached_no_trigger")
                             and e.get("ttl", 0) > 0]
                if len(same_path) >= 3:
                    return {
                        "action": "change_seed",
                        "reason": f"Ranked path {ranked_path_id} has {len(same_path)} no-crash attempts; try a different seed or approach",
                        "negative_evidence_kind": "repeated_candidate",
                        "blocks_submit": False,
                        "target_ids": {"ranked_path_id": ranked_path_id},
                        "prompt_instruction": "Try a different seed or mutation strategy; same approach has failed 3+ times.",
                    }

    # 7. Soft actions based on failed gate
    if failed_gate == "carrier_parse":
        return {
            "action": "repair_carrier",
            "reason": "Submit returned carrier/parser error",
            "negative_evidence_kind": "format_error",
            "blocks_submit": False,
            "target_ids": {},
            "prompt_instruction": "Fix carrier format before resubmitting.",
        }

    # 8. No hard block — submit allowed
    # But suggest soft actions if there's room for improvement
    soft_action = _soft_action_if_applicable(state)
    if soft_action:
        return soft_action

    return {
        "action": "submit_allowed",
        "reason": "",
        "negative_evidence_kind": "",
        "blocks_submit": False,
        "target_ids": {},
        "prompt_instruction": "",
    }


def _best_ranked_path_id(state: CyberGymState) -> str:
    """Get the best current ranked path ID."""
    paths = list(getattr(state, "ranked_vulnerability_paths", []) or [])
    if paths:
        return str(paths[0].get("path_id", ""))
    return ""


def _latest_frontier_probe(state: CyberGymState) -> dict[str, Any]:
    probes = (getattr(state, "metadata", {}) or {}).get("frontier_probes", [])
    if isinstance(probes, list) and probes:
        latest = probes[-1]
        return latest if isinstance(latest, dict) else {}
    return {}


def _frontier_negative_kind(status: str) -> str:
    return {
        "wrong_harness": "wrong_harness_binary",
        "path_not_reached": "path_not_reached",
        "trigger_unmet": "objective_not_satisfied",
        "oracle_not_observable": "objective_not_observable",
        "frontier_unknown": "frontier_unknown",
        "unknown": "frontier_unknown",
    }.get(status, "frontier_unknown")


def _normalize_frontier_status(status: str) -> str:
    return {
        "harness_not_reached": "path_not_reached",
        "parser_rejected": "path_not_reached",
        "dispatch_not_selected": "path_not_reached",
        "sink_not_reached": "path_not_reached",
        "sink_reached_trigger_unmet": "trigger_unmet",
        "capability_error": "frontier_unknown",
        "inconclusive": "frontier_unknown",
    }.get(status, status)


def _runtime_diagnosis_action_if_applicable(
    state: CyberGymState,
    failed_gate: str,
) -> dict[str, Any] | None:
    if failed_gate not in {
        "no_crash_unknown",
        "path_not_reached",
        "trigger_wrong_signature",
        "trigger_wrong_location",
    }:
        return None
    if not _staged_runtime_ready(state):
        return None

    candidate_path = _latest_feedback_candidate_path(state)
    if not candidate_path:
        return None

    objective_id = _active_objective_id(state)
    ranked_path_id = _best_ranked_path_id(state)
    latest_evidence = _latest_runtime_evidence_for_candidate(state, candidate_path)
    latest_probe = _latest_frontier_probe_for_candidate(state, candidate_path)

    if not latest_evidence:
        return {
            "action": "run_candidate",
            "reason": (
                f"Latest submit returned {failed_gate}; staged binary is available, "
                "so classify whether the same candidate exits cleanly, is rejected, "
                "times out, or crashes before replanning."
            ),
            "negative_evidence_kind": "frontier_unknown",
            "blocks_submit": True,
            "target_ids": {
                "candidate_path": candidate_path,
                "objective_id": objective_id,
                "ranked_path_id": ranked_path_id,
            },
            "prompt_instruction": (
                f"Call run_candidate(candidate_path={candidate_path!r}, "
                f"objective_id={objective_id!r}, purpose='classify_no_trigger'). "
                "Only skip this if you first make a source-backed repair that invalidates the candidate."
            ),
        }

    outcome = str(latest_evidence.get("conclusion") or latest_evidence.get("status") or "")
    capability = (state.metadata or {}).get(STAGED_BINARY_CAPABILITY) or {}
    gdb_available = isinstance(capability, dict) and bool(capability.get("gdb_available"))
    if (
        gdb_available
        and latest_probe is None
        and outcome in {"clean_exit", "input_rejected", "timeout", "profile_unresolved", "environment_error"}
    ):
        return {
            "action": "probe_runtime_frontier",
            "reason": (
                f"run_candidate outcome={outcome}; use source-backed GDB frontier probes "
                "to find the first unreached harness/parser/sink boundary."
            ),
            "negative_evidence_kind": _runtime_outcome_negative_kind(outcome),
            "blocks_submit": True,
            "target_ids": {
                "candidate_path": candidate_path,
                "objective_id": objective_id,
                "ranked_path_id": ranked_path_id,
            },
            "prompt_instruction": (
                f"Call probe_runtime_frontier(candidate_path={candidate_path!r}, "
                f"objective_id={objective_id!r}, path_id={ranked_path_id!r}) and repair the "
                "first_unreached_role before submitting another variant."
            ),
        }

    return None


def _staged_runtime_ready(state: CyberGymState) -> bool:
    metadata = getattr(state, "metadata", {}) or {}
    if metadata.get("_need_container_rediscovery"):
        return True
    capability = metadata.get(STAGED_BINARY_CAPABILITY) or {}
    profile = metadata.get(INVOCATION_PROFILE) or {}
    return (
        isinstance(capability, dict)
        and bool(capability.get("available"))
        and isinstance(profile, dict)
        and profile.get("mode") in {"argv_file", "stdin"}
    )


def _latest_feedback_candidate_path(state: CyberGymState) -> str:
    hot = list(getattr(state, "hot_feedback_window", []) or [])
    for item in reversed(hot):
        path = str(getattr(item, "poc_path", "") or "").strip()
        if path:
            return path

    for fact in reversed(list(getattr(state, "durable_feedback_facts", []) or [])):
        text = str(fact or "").strip()
        if text.startswith("feedback_poc_path:"):
            return text.split(":", 1)[1].strip()

    ready = list(getattr(state, "ready_pocs", []) or [])
    for item in reversed(ready):
        path = str(getattr(item, "file_path", "") or "").strip()
        if path:
            return path
    return ""


def _ensure_transient_objective_for_feedback(
    state: CyberGymState,
    candidate_path: str,
) -> None:
    """Create a lightweight objective anchor for no-trigger diagnosis.

    Dynamic diagnosis and frontier probes need a stable objective id.  Remote
    traces showed repeated no-trigger loops with ``Active objective: (none)``,
    so synthesize a conservative objective from the current sink/path/candidate
    when the model has not recorded one explicitly.
    """
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    if any(isinstance(obj, dict) and obj.get("status") == "active" for obj in objectives):
        return

    ranked_path_id = _best_ranked_path_id(state)
    target_function = ""
    target_file = ""
    target_line = 0

    sinks = []
    if hasattr(state, "confirmed_sink_candidates"):
        try:
            sinks = list(state.confirmed_sink_candidates() or [])
        except Exception:
            sinks = []
    if not sinks:
        sinks = [
            item for item in list(getattr(state, "sink_candidates", []) or [])
            if str(getattr(item, "status", "") or "") in {"confirmed", "candidate"}
        ]

    if sinks:
        sink = sinks[0]
        target_function = str(getattr(sink, "function", "") or "")
        target_file = str(getattr(sink, "file", "") or "")
        target_line = int(getattr(sink, "line", 0) or 0)
        if (not target_file or not target_line) and getattr(sink, "location", ""):
            loc_file, loc_line = _split_location(str(getattr(sink, "location") or ""))
            target_file = target_file or loc_file
            target_line = target_line or loc_line
        ranked_path_id = ranked_path_id or str((getattr(sink, "metadata", {}) or {}).get("ranked_path_id") or "")
    else:
        ranked_paths = list(getattr(state, "ranked_vulnerability_paths", []) or [])
        if ranked_paths:
            path = ranked_paths[0]
            endpoint = path.get("endpoint") or {}
            target_function = str(endpoint.get("function") or path.get("function") or "")
            target_file = str(endpoint.get("file") or path.get("file") or "")
            target_line = int(endpoint.get("line") or path.get("line") or 0)
            ranked_path_id = ranked_path_id or str(path.get("path_id") or "")

    seed = "|".join(
        [
            str(getattr(state, "task_id", "") or ""),
            candidate_path or str(getattr(state, "last_submitted_poc_path", "") or ""),
            ranked_path_id,
            target_function,
            target_file,
            str(target_line or ""),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    kind = str((getattr(state, "metadata", {}) or {}).get("crash_type_prior") or getattr(state, "bug_type", "") or "unknown_crash")
    objective = {
        "objective_id": f"auto_{digest}",
        "status": "active",
        "kind": kind or "unknown_crash",
        "target_function": target_function,
        "target_file": target_file,
        "target_line": target_line,
        "ranked_path_id": ranked_path_id,
        "input_fields": [],
        "source": "auto_feedback_no_trigger",
        "candidate_path": candidate_path,
    }
    state.active_trigger_objectives = [*objectives, objective][-8:]
    try:
        from ..core.runtime_context_contract import bump_context_revision

        bump_context_revision(state, "trigger_objectives")
    except Exception:
        pass


def _split_location(location: str) -> tuple[str, int]:
    raw_file, sep, raw_line = str(location or "").rpartition(":")
    if sep and raw_line.isdigit():
        return raw_file, int(raw_line)
    return str(location or ""), 0


def _active_objective_id(state: CyberGymState) -> str:
    for obj in list(getattr(state, "active_trigger_objectives", []) or []):
        if isinstance(obj, dict) and obj.get("status") == "active":
            return str(obj.get("objective_id") or "")
    return ""


def _latest_runtime_evidence_for_candidate(
    state: CyberGymState,
    candidate_path: str,
) -> dict[str, Any] | None:
    records = (getattr(state, "metadata", {}) or {}).get(RUNTIME_EVIDENCE, [])
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        recorded_path = str(record.get("candidate_path") or "").strip()
        if recorded_path and _same_candidate_path(recorded_path, candidate_path):
            return record
    return None


def _latest_frontier_probe_for_candidate(
    state: CyberGymState,
    candidate_path: str,
) -> dict[str, Any] | None:
    probes = (getattr(state, "metadata", {}) or {}).get(FRONTIER_PROBES, [])
    if not isinstance(probes, list):
        return None
    for record in reversed(probes):
        if not isinstance(record, dict):
            continue
        recorded_path = str(record.get("candidate_path") or "").strip()
        if recorded_path and _same_candidate_path(recorded_path, candidate_path):
            return record
    return None


def _same_candidate_path(left: str, right: str) -> bool:
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


def _runtime_outcome_negative_kind(outcome: str) -> str:
    return {
        "clean_exit": "path_reached_no_trigger",
        "input_rejected": "path_not_reached",
        "timeout": "frontier_unknown",
        "profile_unresolved": "wrong_harness_binary",
        "environment_error": "frontier_unknown",
    }.get(outcome, "frontier_unknown")


def _soft_action_if_applicable(state: CyberGymState) -> dict[str, Any] | None:
    """Suggest a soft (non-blocking) action if applicable."""
    # Suggest harness protocol extraction if unknown
    fmt = getattr(state, "input_format", None)
    if fmt:
        consumption = getattr(fmt, "consumption", None)
        if consumption:
            scope = str(getattr(consumption, "endpoint_scope", "") or "")
            if not scope and str(getattr(consumption, "pattern", "") or "") == "unknown":
                return {
                    "action": "extract_harness_protocol",
                    "reason": "Harness consumption model is unresolved",
                    "negative_evidence_kind": "",
                    "blocks_submit": False,
                    "target_ids": {},
                    "prompt_instruction": "Read the harness main function to determine input consumption pattern.",
                }

    # Suggest local test mining if we have source but no local tests
    local_refs = list(getattr(state, "local_mining_refs", []) or [])
    if not local_refs:
        # Check if there's a tests/ directory or similar
        import os
        repo_dir = str(getattr(state, "repo_dir", "") or "")
        if repo_dir and os.path.isdir(os.path.join(repo_dir, "tests")):
            return {
                "action": "mine_local_tests",
                "reason": "Local test directory exists but has not been mined for examples",
                "negative_evidence_kind": "",
                "blocks_submit": False,
                "target_ids": {},
                "prompt_instruction": "Look at local test files for input format and mutation examples.",
            }

    return None
