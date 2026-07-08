"""Pack-aware submit feedback taxonomy.

This module converts submit feedback plus pack validation state into a compact
action record for runtime observation. It is intentionally advisory: existing
feedback arbitration still controls hard blocking.
"""

from __future__ import annotations

from typing import Any

from ..core.metadata_keys import RUNTIME_EVIDENCE


def derive_pack_feedback_action(
    *,
    state: Any,
    submit_result: dict[str, Any],
    failed_gate: str,
) -> dict[str, Any]:
    return {}  # Pack knowledge disabled
    if isinstance(pack_validation, dict):
        validation_action = _from_pack_validation(pack_validation, pack_id)
        if validation_action:
            return validation_action

    gate = failed_gate or _fallback_failed_gate(submit_result)
    if gate == "carrier_parse":
        return _action(
            pack_id=pack_id,
            category="carrier_parse",
            action="repair_pack_carrier",
            reason="Submit feedback indicates the carrier failed before reaching the trigger path.",
            blocks_submit=True,
            prompt="Repair the active pack carrier invariants before another submit.",
        )
    if gate in {"trigger_wrong_signature", "wrong_trigger", "candidate_rejected"}:
        return _action(
            pack_id=pack_id,
            category="oracle_mismatch",
            action="verify_pack_oracle",
            reason="The candidate produced a crash/rejection signal that does not match the expected oracle.",
            blocks_submit=False,
            prompt="Refine the trigger toward the expected oracle/crash signature for the active pack.",
        )
    if gate == "trigger_wrong_location":
        return _action(
            pack_id=pack_id,
            category="path_not_reached",
            action="route_to_pack_parser_path",
            reason="The crash location suggests the active pack routed input away from the intended vulnerable path.",
            blocks_submit=False,
            prompt="Adjust selector/object/path fields that route the carrier to the vulnerable parser path.",
        )

    diagnosis = _active_no_trigger_diagnosis(state)
    if diagnosis in {"oracle_not_observable"}:
        return _action(
            pack_id=pack_id,
            category="oracle_mismatch",
            action="verify_pack_oracle",
            reason=f"Active objective no-trigger diagnosis is {diagnosis}.",
            blocks_submit=True,
            prompt="Verify that submit_poc can observe this oracle or switch objective.",
        )
    if diagnosis in {"path_not_reached", "wrong_harness"}:
        return _action(
            pack_id=pack_id,
            category="path_not_reached",
            action="route_to_pack_parser_path",
            reason=f"Active objective no-trigger diagnosis is {diagnosis}.",
            blocks_submit=False,
            prompt="Fix harness selector/path fields before changing trigger bytes.",
        )
    if diagnosis in {"trigger_condition_unmet"}:
        return _action(
            pack_id=pack_id,
            category="trigger_unmet",
            action="adjust_pack_trigger",
            reason=f"Active objective no-trigger diagnosis is {diagnosis}.",
            blocks_submit=False,
            prompt="Keep the carrier/path stable and adjust the trigger value, size, offset, or state.",
        )

    runtime_category = _from_runtime_evidence(state)
    if runtime_category:
        return runtime_category | {"pack_id": pack_id or runtime_category.get("pack_id", "")}

    if gate in {"no_crash_unknown", "path_not_reached"}:
        return _action(
            pack_id=pack_id,
            category="unknown_no_crash",
            action="classify_pack_miss",
            reason="No crash observed; current evidence does not distinguish path_not_reached from trigger_unmet.",
            blocks_submit=False,
            prompt="Use source evidence, pack validation, or gdb_debug to classify path reachability vs trigger failure.",
        )
    return {}


def _from_pack_validation(pack_validation: dict[str, Any], pack_id: str) -> dict[str, Any]:
    if pack_validation.get("overall_verdict") != "fail":
        return {}
    findings = [item for item in list(pack_validation.get("findings") or []) if isinstance(item, dict)]
    fail_findings = [item for item in findings if item.get("verdict") == "fail"]
    if not fail_findings:
        return {}
    first = fail_findings[0]
    layer = str(first.get("layer") or "")
    validator_id = str(first.get("validator_id") or "")
    repairs = [item for item in list(pack_validation.get("repairs") or []) if isinstance(item, dict)]
    target = str((repairs[0] or {}).get("description") or first.get("repair_actions") or "") if repairs else str(first.get("repair_actions") or "")
    if layer == "mutation_intent" or "raw_marker" in validator_id:
        return _action(
            pack_id=pack_id or str(pack_validation.get("pack_id") or ""),
            category="mutation_lost",
            action="reapply_pack_mutation",
            reason=str(first.get("evidence_ref") or "pack validation found mutation intent missing"),
            blocks_submit=True,
            prompt=target or "Reapply the declared raw trigger after carrier repair.",
        )
    return _action(
        pack_id=pack_id or str(pack_validation.get("pack_id") or ""),
        category="carrier_parse",
        action="repair_pack_carrier",
        reason=str(first.get("evidence_ref") or "pack validation failed"),
        blocks_submit=True,
        prompt=target or "Repair active pack carrier invariants before another submit.",
    )


def _from_runtime_evidence(state: Any) -> dict[str, Any]:
    records = list((getattr(state, "metadata", {}) or {}).get(RUNTIME_EVIDENCE, []) or [])
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        text = " ".join(
            str(record.get(key) or "")
            for key in ("outcome", "status", "assessment", "summary", "source_kind")
        ).lower()
        if "sink_reached_trigger_unmet" in text or "path_reached_no_trigger" in text:
            return _action(
                pack_id="",
                category="trigger_unmet",
                action="adjust_pack_trigger",
                reason="Runtime evidence indicates the path was reached but trigger condition was unmet.",
                blocks_submit=False,
                prompt="Preserve carrier/path and adjust trigger bytes or state.",
            )
        if "path_not_reached" in text or "breakpoint_missed" in text:
            return _action(
                pack_id="",
                category="path_not_reached",
                action="route_to_pack_parser_path",
                reason="Runtime evidence indicates the vulnerable path was not reached.",
                blocks_submit=False,
                prompt="Fix selector/path fields that route the active carrier to the vulnerable parser path.",
            )
    return {}


def _active_pack_id(state: Any) -> str:
    pack_mode = getattr(state, "pack_mode", {}) or {}
    if isinstance(pack_mode, dict):
        return str(pack_mode.get("pack_id") or "")
    return ""


def _active_no_trigger_diagnosis(state: Any) -> str:
    for obj in list(getattr(state, "active_trigger_objectives", []) or []):
        if isinstance(obj, dict) and obj.get("status") == "active":
            return str(obj.get("no_trigger_diagnosis") or "")
    return ""


def _fallback_failed_gate(submit_result: dict[str, Any]) -> str:
    if submit_result.get("status") == "error":
        return "carrier_parse"
    if submit_result.get("vul_exit_code") in (None, 0):
        return "no_crash_unknown"
    return "wrong_trigger"


def _action(
    *,
    pack_id: str,
    category: str,
    action: str,
    reason: str,
    blocks_submit: bool,
    prompt: str,
) -> dict[str, Any]:
    return {
        "pack_id": pack_id,
        "category": category,
        "action": action,
        "reason": reason[:240],
        "blocks_submit": bool(blocks_submit),
        "prompt_instruction": prompt[:240],
    }
