#!/usr/bin/env python3
"""Offline evaluation for capability casebook.

Evaluates whether agent state snapshots contain the expected
capabilities.  Supports two modes:
  --casebook FILE  Read JSONL casebook (from build_timeout_casebook.py)
  --fixtures DIR   Read JSONL fixture files from tests/fixtures/structured_casebook/

Can also evaluate real observation/state snapshots via --snapshots DIR.

Does NOT depend on network or external benchmark environments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Capability check registry
# ------------------------------------------------------------------

def _check_mechanism_graph(snapshot: dict) -> bool:
    return bool(snapshot.get("crash_mechanism_graphs"))

def _check_trigger_objective(snapshot: dict) -> bool:
    return bool(snapshot.get("active_trigger_objectives"))

def _check_origin_use_objective(snapshot: dict) -> bool:
    return any(
        obj.get("kind") == "origin_use"
        for obj in snapshot.get("active_trigger_objectives", [])
    )

def _check_oracle_aware(snapshot: dict) -> bool:
    """Check if any objective has oracle_kind and no_trigger_diagnosis."""
    objectives = snapshot.get("active_trigger_objectives", [])
    return any(
        obj.get("oracle_kind") and obj.get("no_trigger_diagnosis")
        for obj in objectives
    )

def _check_msan_objective(snapshot: dict) -> bool:
    return any(
        obj.get("oracle_kind") == "msan"
        for obj in snapshot.get("active_trigger_objectives", [])
    )

def _check_protocol_transcript(snapshot: dict) -> bool:
    return bool(snapshot.get("protocol_transcript_plans"))

def _check_structured_rewrite(snapshot: dict) -> bool:
    return bool(snapshot.get("structured_rewrite_plans"))

def _check_consistency_guard(snapshot: dict) -> bool:
    return any(
        sig.get("blocks_submit") or sig.get("severity") == "block"
        for sig in snapshot.get("consistency_signals", [])
    )

def _check_length_mapping(snapshot: dict) -> bool:
    return any(
        m.get("argument_role") == "length"
        for m in snapshot.get("active_input_mappings", [])
    )

def _check_selector_mapping(snapshot: dict) -> bool:
    return any(
        m.get("argument_role") == "selector"
        for m in snapshot.get("active_input_mappings", [])
    )

def _check_carrier_stack(snapshot: dict) -> bool:
    return bool(
        snapshot.get("carrier_stack") or
        (isinstance(snapshot.get("harness_protocols"), list) and
         any(p.get("carrier_stack") for p in snapshot["harness_protocols"]))
    )

def _check_scope_mismatch_guard(snapshot: dict) -> bool:
    return any(
        sig.get("kind") in ("scope_mismatch", "wrong_format_scope")
        for sig in snapshot.get("consistency_signals", [])
    )

def _check_transcript_gap_next_action(snapshot: dict) -> bool:
    return bool(
        snapshot.get("protocol_transcript_plans") and
        any(not t.get("steps") or len(t.get("steps", [])) < 2
            for t in snapshot.get("protocol_transcript_plans", []))
    )

def _check_harness_protocol(snapshot: dict) -> bool:
    protocols = snapshot.get("harness_protocols", [])
    return isinstance(protocols, list) and len(protocols) > 0 and any(
        p.get("input_contract") != "unknown" for p in protocols
    )

def _check_api_reachability(snapshot: dict) -> bool:
    return bool(snapshot.get("api_reachability") or _metadata(snapshot).get("api_reachability"))

def _check_call_path(snapshot: dict) -> bool:
    objectives = snapshot.get("active_trigger_objectives", [])
    return any(
        obj.get("call_path_evidence") for obj in objectives
    ) or bool(_metadata(snapshot).get("call_path_evidence"))

def _check_numeric_constraints(snapshot: dict) -> bool:
    return bool(snapshot.get("numeric_constraints") or _metadata(snapshot).get("numeric_constraints"))

def _check_format_template(snapshot: dict) -> bool:
    recipe = snapshot.get("poc_recipe", {}) or _metadata(snapshot).get("poc_recipe", {})
    return bool(recipe and recipe.get("carrier", {}).get("format"))

def _check_candidate_builder(snapshot: dict) -> bool:
    return bool(
        snapshot.get("ready_pocs")
        or snapshot.get("candidate_built_from_recipe")
        or _metadata(snapshot).get("last_poc_build_result", {}).get("status") == "success"
    )

def _check_local_mining(snapshot: dict) -> bool:
    return bool(snapshot.get("local_mining_refs"))

def _check_feedback_action_runner(snapshot: dict) -> bool:
    metadata = _metadata(snapshot)
    return bool(metadata.get("last_feedback_action_result"))

def _check_transcript_plan(snapshot: dict) -> bool:
    plans = snapshot.get("protocol_transcript_plans", [])
    return any(len(p.get("steps", [])) >= 2 for p in plans)


CAPABILITY_CHECKS: dict[str, Any] = {
    "mechanism_graph": _check_mechanism_graph,
    "trigger_objective": _check_trigger_objective,
    "origin_use_objective": _check_origin_use_objective,
    "oracle_aware": _check_oracle_aware,
    "msan_objective": _check_msan_objective,
    "protocol_transcript": _check_protocol_transcript,
    "structured_rewrite": _check_structured_rewrite,
    "consistency_guard": _check_consistency_guard,
    "length_mapping": _check_length_mapping,
    "selector_mapping": _check_selector_mapping,
    "carrier_stack": _check_carrier_stack,
    "scope_mismatch_guard": _check_scope_mismatch_guard,
    "transcript_gap_next_action": _check_transcript_gap_next_action,
    "harness_protocol": _check_harness_protocol,
    "api_reachability": _check_api_reachability,
    "call_path": _check_call_path,
    "numeric_constraints": _check_numeric_constraints,
    "format_template": _check_format_template,
    "candidate_builder": _check_candidate_builder,
    "local_mining": _check_local_mining,
    "feedback_action_runner": _check_feedback_action_runner,
    "transcript_plan": _check_transcript_plan,
}


def _metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata = snapshot.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


# ------------------------------------------------------------------
# Evaluation
# ------------------------------------------------------------------

def evaluate_state_snapshot(snapshot: dict[str, Any], expected: list[str]) -> dict[str, Any]:
    """Evaluate whether a state snapshot contains expected capabilities."""
    checks: dict[str, bool] = {}

    for key, checker in CAPABILITY_CHECKS.items():
        try:
            checks[key] = checker(snapshot)
        except Exception:
            checks[key] = False

    passed = all(checks.get(key, False) for key in expected)
    missing = [key for key in expected if not checks.get(key, False)]

    return {
        "passed": passed,
        "checks": checks,
        "missing": missing,
    }


def evaluate_observation_visibility(observation_text: str) -> dict[str, bool]:
    """Check if capabilities are visible in observation text."""
    lower = observation_text.lower()
    return {
        "objective_visible": "objective" in lower,
        "transcript_visible": "transcript" in lower or "step order" in lower,
        "consistency_block_visible": "consistency" in lower and "block" in lower,
        "recipe_visible": "recipe" in lower,
        "local_mining_visible": "regression_test" in lower or "harness_protocol" in lower or "mining" in lower,
        "next_action_specific": "required:" in lower and "stop condition:" in lower,
        "oracle_visible": "oracle" in lower,
        "no_trigger_diagnosis_visible": "no-trigger diagnosis" in lower or "no_trigger_diagnosis" in lower,
    }


def extract_next_action(snapshot: dict[str, Any], observation_text: str = "") -> str:
    metadata = _metadata(snapshot)
    action = metadata.get("last_feedback_action", {})
    if isinstance(action, dict) and action.get("action"):
        return str(action.get("action") or "")
    lower = observation_text.lower()
    known = [
        "verify_oracle_context",
        "extract_harness_protocol",
        "localize_field",
        "repair_carrier",
        "repair_consistency",
        "mine_local_tests",
        "complete_transcript",
        "switch_objective",
    ]
    for name in known:
        if name in lower:
            return name
    return ""


def load_casebook(casebook_path: str) -> list[dict[str, Any]]:
    """Load cases from a JSONL casebook file."""
    cases: list[dict[str, Any]] = []
    with open(casebook_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return cases


def load_fixtures(fixtures_dir: Path) -> list[dict[str, Any]]:
    """Load casebook cases from JSONL fixture files."""
    cases: list[dict[str, Any]] = []
    for jsonl_file in sorted(fixtures_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cases.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return cases


def run_evaluation(
    cases: list[dict[str, Any]],
    snapshot_provider: Any = None,
    observation_provider: Any = None,
) -> dict[str, Any]:
    """Run evaluation across all cases.

    If snapshot_provider is None, uses a synthetic snapshot that
    exercises all capabilities.
    """
    results: list[dict[str, Any]] = []
    coverage: dict[str, float] = {}
    family_coverage: dict[str, dict[str, int]] = {}
    next_action_correct = 0
    forbidden_action_present = 0
    runner_executed = 0
    diagnosis_visible = 0

    for case in cases:
        case_id = case.get("case_id", "unknown")
        expected = case.get("expected", case.get("required_capabilities", []))
        family = case.get("family", case.get("failure_family", ""))

        if snapshot_provider:
            snapshot = snapshot_provider(case)
        else:
            snapshot = _synthetic_snapshot(case)

        eval_result = evaluate_state_snapshot(snapshot, expected)
        observation_text = observation_provider(case) if observation_provider else ""
        visibility = evaluate_observation_visibility(observation_text) if observation_text else {}
        next_action = extract_next_action(snapshot, observation_text)
        expected_action = str(case.get("expected_next_action", "") or "")
        forbidden = [str(item) for item in case.get("forbidden_next_actions", []) or []]
        forbidden_present = _forbidden_action_present(next_action, observation_text, forbidden)
        runner_ok = _metadata(snapshot).get("last_feedback_action_result", {}).get("status") in {"executed", "blocked"}
        diagnosis_ok = bool(
            visibility.get("no_trigger_diagnosis_visible")
            or visibility.get("oracle_visible")
            or _metadata(snapshot).get("frontier_probes")
            or _metadata(snapshot).get("oracle_assessments")
        )
        if expected_action and next_action == expected_action:
            next_action_correct += 1
        if forbidden_present:
            forbidden_action_present += 1
        if runner_ok:
            runner_executed += 1
        if diagnosis_ok:
            diagnosis_visible += 1
        results.append({
            "case_id": case_id,
            "family": family,
            "passed": eval_result["passed"],
            "missing": eval_result["missing"],
            "checks": eval_result["checks"],
            "capabilities_visible": eval_result["checks"],
            "next_action": next_action,
            "expected_next_action": expected_action,
            "next_action_correct": bool(expected_action and next_action == expected_action),
            "forbidden_action_present": forbidden_present,
            "runner_executed": runner_ok,
            "diagnosis_visible": diagnosis_ok,
            "observation_visibility": visibility,
        })

        # Update capability coverage
        for key in expected:
            if key not in coverage:
                coverage[key] = 0.0
            if eval_result["checks"].get(key, False):
                coverage[key] += 1.0

        # Update family coverage
        if family:
            if family not in family_coverage:
                family_coverage[family] = {"total": 0, "passed": 0}
            family_coverage[family]["total"] += 1
            if eval_result["passed"]:
                family_coverage[family]["passed"] += 1

    # Normalize coverage
    total = max(len(cases), 1)
    for key in coverage:
        coverage[key] = round(coverage[key] / total, 2)

    passed_count = sum(1 for r in results if r["passed"])

    # Compute aggregate metrics
    metrics = {
        "capability_visible_rate": round(passed_count / total, 3) if total else 0,
        "next_action_correct_rate": round(next_action_correct / total, 3) if total else 0,
        "forbidden_submit_rate": round(forbidden_action_present / total, 3) if total else 0,
        "runner_executed_rate": round(runner_executed / total, 3) if total else 0,
        "diagnosis_visible_rate": round(diagnosis_visible / total, 3) if total else 0,
        "total_cases": len(cases),
        "passed_cases": passed_count,
        "families_with_coverage": sum(
            1 for f, d in family_coverage.items() if d["passed"] > 0
        ),
        "families_total": len(family_coverage),
    }

    return {
        "total": len(cases),
        "passed": passed_count,
        "coverage": coverage,
        "family_coverage": family_coverage,
        "metrics": metrics,
        "cases": results,
    }


def _forbidden_action_present(next_action: str, observation_text: str, forbidden: list[str]) -> bool:
    lower = observation_text.lower()
    for item in forbidden:
        needle = item.lower()
        if not needle:
            continue
        if needle == next_action.lower():
            return True
        if needle in lower:
            return True
        if needle == "submit_ready_poc_without_recipe_change" and "submit now" in lower:
            return True
    return False


def snapshot_provider_from_dir(snapshot_dir: Path):
    index = _index_json_sidecars(snapshot_dir)

    def provider(case: dict[str, Any]) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "")
        path = (
            index.get(case_id)
            or index.get(f"arvo_{case_id}")
            or index.get(f"state_{case_id}")
        )
        if not path:
            return {}
        try:
            payload = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict) and isinstance(payload.get("state"), dict):
            return payload["state"]
        return payload if isinstance(payload, dict) else {}

    return provider


def observation_provider_from_dir(observation_dir: Path):
    index = _index_observation_sidecars(observation_dir)

    def provider(case: dict[str, Any]) -> str:
        case_id = str(case.get("case_id") or "")
        path = index.get(case_id) or index.get(f"arvo_{case_id}") or index.get(f"observation_{case_id}")
        if not path:
            return ""
        try:
            return path.read_text(errors="replace")
        except OSError:
            return ""

    return provider


def _index_json_sidecars(root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in root.rglob("*.json"):
        stem = path.stem
        for key in {stem, stem.replace("state_", ""), stem.replace("arvo_", "")}:
            if key and key not in index:
                index[key] = path
        for part in path.parts:
            if part.startswith("arvo_"):
                index.setdefault(part.replace("arvo_", ""), path)
                index.setdefault(part, path)
    return index


def _index_observation_sidecars(root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for pattern in ("*.txt", "*.md", "*.log"):
        for path in root.rglob(pattern):
            stem = path.stem
            for key in {stem, stem.replace("observation_", ""), stem.replace("arvo_", "")}:
                if key and key not in index:
                    index[key] = path
            for part in path.parts:
                if part.startswith("arvo_"):
                    index.setdefault(part.replace("arvo_", ""), path)
                    index.setdefault(part, path)
    return index


def _synthetic_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    """Generate a synthetic snapshot that has all capabilities populated.
    Used for smoke testing the evaluator itself."""
    family = case.get("family", case.get("failure_family", ""))
    expected = case.get("expected", case.get("required_capabilities", []))

    snapshot: dict[str, Any] = {
        "crash_mechanism_graphs": [
            {"graph_id": "mg_001", "mechanism_family": family, "nodes": [], "missing_roles": []},
        ],
        "active_trigger_objectives": [
            {"objective_id": "obj_001", "kind": "bounds", "status": "active",
             "oracle_kind": "asan", "no_trigger_diagnosis": "trigger_condition_unmet",
             "input_fields": []},
        ],
        "protocol_transcript_plans": [],
        "structured_rewrite_plans": [],
        "consistency_signals": [],
        "active_input_mappings": [],
        "harness_protocols": [],
    }

    # Populate based on expected
    for key in expected:
        if key == "origin_use_objective":
            snapshot["active_trigger_objectives"].append({
                "objective_id": "obj_msan", "kind": "origin_use", "status": "active",
                "oracle_kind": "msan", "no_trigger_diagnosis": "oracle_not_observable",
                "input_fields": [],
            })
        elif key == "oracle_aware":
            snapshot["active_trigger_objectives"][0]["oracle_kind"] = "msan"
            snapshot["active_trigger_objectives"][0]["no_trigger_diagnosis"] = "oracle_not_observable"
        elif key == "msan_objective":
            snapshot["active_trigger_objectives"].append({
                "objective_id": "obj_msan2", "kind": "origin_use", "status": "active",
                "oracle_kind": "msan", "no_trigger_diagnosis": "oracle_not_observable",
                "input_fields": [],
            })
        elif key == "protocol_transcript":
            snapshot["protocol_transcript_plans"] = [
                {"transcript_id": "tr_001", "status": "active", "steps": [{"role": "init"}, {"role": "send_frame"}]},
            ]
        elif key == "structured_rewrite":
            snapshot["structured_rewrite_plans"] = [
                {"rewrite_id": "rw_001", "status": "active", "operations": [{"kind": "set_u32"}], "invariants": []},
            ]
        elif key == "consistency_guard":
            snapshot["consistency_signals"] = [
                {"signal_id": "cs_001", "kind": "scope_mismatch", "severity": "block", "blocks_submit": True},
            ]
        elif key == "length_mapping":
            snapshot["active_input_mappings"] = [
                {"mapping_id": "map_001", "argument_role": "length", "status": "resolved"},
            ]
        elif key == "selector_mapping":
            snapshot["active_input_mappings"].append(
                {"mapping_id": "map_sel", "argument_role": "selector", "status": "resolved"},
            )
        elif key == "carrier_stack":
            snapshot["harness_protocols"] = [
                {"protocol_id": "hp_001", "carrier_stack": ["jnx", "dcm"], "input_contract": "buffer"},
            ]
        elif key == "scope_mismatch_guard":
            snapshot["consistency_signals"] = [
                {"signal_id": "cs_002", "kind": "scope_mismatch", "severity": "block", "blocks_submit": True},
            ]
        elif key == "transcript_gap_next_action":
            snapshot["protocol_transcript_plans"] = [
                {"transcript_id": "tr_gap", "status": "active", "steps": [{"role": "init"}]},
            ]
        elif key == "harness_protocol":
            snapshot["harness_protocols"] = [
                {"protocol_id": "hp_001", "input_contract": "buffer", "carrier_stack": []},
            ]
        elif key == "api_reachability":
            snapshot["api_reachability"] = {"harness_apis": [{"selector": "arch", "cases": ["x86"]}], "arch_selectors": []}
        elif key == "call_path":
            snapshot["active_trigger_objectives"][0]["call_path_evidence"] = [{"from": "main", "to": "sink"}]
        elif key == "numeric_constraints":
            snapshot["numeric_constraints"] = [{"constraint_id": "nc_001", "kind": "overflow"}]
        elif key == "format_template":
            snapshot["poc_recipe"] = {"recipe_id": "rec_001", "carrier": {"format": "tiff"}}
        elif key == "candidate_builder":
            snapshot["ready_pocs"] = [{"candidate_id": "c1", "generation_method": "recipe"}]
        elif key == "local_mining":
            snapshot["local_mining_refs"] = [{"kind": "regression_test", "path": "tests/test_foo.c"}]
        elif key == "feedback_action_runner":
            snapshot["metadata"] = {"last_feedback_action_result": {"status": "executed", "action": "mine_local_tests"}}
        elif key == "transcript_plan":
            snapshot["protocol_transcript_plans"] = [
                {"transcript_id": "tr_001", "status": "active", "steps": [{"role": "init"}, {"role": "send"}]},
            ]

    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate capability casebook")
    parser.add_argument("--casebook", type=str, help="Path to JSONL casebook file")
    parser.add_argument("--fixtures", type=str, help="Path to fixtures directory")
    parser.add_argument("--snapshots", type=str, help="Path to run snapshot directory")
    parser.add_argument("--observations", type=str, help="Path to observation sidecar directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    cases: list[dict[str, Any]] = []

    if args.casebook:
        if not os.path.isfile(args.casebook):
            print(f"Error: casebook not found: {args.casebook}", file=sys.stderr)
            sys.exit(1)
        cases = load_casebook(args.casebook)
    elif args.fixtures:
        fixtures_dir = Path(args.fixtures)
        if not fixtures_dir.is_dir():
            print(f"Error: fixtures directory not found: {args.fixtures}", file=sys.stderr)
            sys.exit(1)
        cases = load_fixtures(fixtures_dir)
    else:
        print("Error: --casebook or --fixtures required", file=sys.stderr)
        sys.exit(1)

    if not cases:
        print("No cases found.", file=sys.stderr)
        sys.exit(1)

    snapshot_provider = None
    if args.snapshots:
        snapshot_dir = Path(args.snapshots)
        if not snapshot_dir.is_dir():
            print(f"Error: snapshots directory not found: {args.snapshots}", file=sys.stderr)
            sys.exit(1)
        snapshot_provider = snapshot_provider_from_dir(snapshot_dir)

    observation_provider = None
    if args.observations:
        observation_dir = Path(args.observations)
        if not observation_dir.is_dir():
            print(f"Error: observations directory not found: {args.observations}", file=sys.stderr)
            sys.exit(1)
        observation_provider = observation_provider_from_dir(observation_dir)

    result = run_evaluation(cases, snapshot_provider=snapshot_provider, observation_provider=observation_provider)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        metrics = result["metrics"]
        print(f"Total: {result['total']}, Passed: {result['passed']}")
        print(f"Capability visible rate: {metrics['capability_visible_rate']}")
        print(f"Next action correct rate: {metrics['next_action_correct_rate']}")
        print(f"Forbidden submit rate: {metrics['forbidden_submit_rate']}")
        print(f"Runner executed rate: {metrics['runner_executed_rate']}")
        print(f"Diagnosis visible rate: {metrics['diagnosis_visible_rate']}")
        print(f"Families with coverage: {metrics['families_with_coverage']}/{metrics['families_total']}")
        print()
        print("Capability coverage:")
        for key, val in sorted(result["coverage"].items()):
            print(f"  {key}: {val}")
        print()
        print("Family coverage:")
        for fam, data in sorted(result["family_coverage"].items()):
            pct = round(data["passed"] / max(data["total"], 1), 2)
            print(f"  {fam}: {data['passed']}/{data['total']} ({pct})")
        print()
        # Show failures
        failures = [c for c in result["cases"] if not c["passed"]]
        if failures:
            print(f"Failing cases ({len(failures)}):")
            for case in failures[:20]:
                missing = ", ".join(case["missing"]) if case["missing"] else ""
                print(f"  [{case['case_id']}] {case['family']}: missing {missing}")


if __name__ == "__main__":
    main()
