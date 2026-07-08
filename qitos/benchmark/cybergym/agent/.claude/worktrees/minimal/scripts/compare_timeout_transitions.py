#!/usr/bin/env python3
"""Compare timeout transitions between two CyberGym trace runs.

The comparison is intentionally independent from ground-truth sink files.  It
answers the rollout gate question for v14-style dynamic diagnosis:

- Which tasks moved from ``budget_time`` to success/non-timeout?
- Which submitted timeouts gained dynamic evidence?
- Did no-submit timeouts become submitted/dynamically diagnosed?
- Did dynamic tools introduce obvious overhead without changing outcomes?
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_dynamic_evidence import summarize_roots  # noqa: E402


def compare_timeout_transitions(
    *,
    old_name: str,
    old_trace_roots: list[str],
    new_name: str,
    new_trace_roots: list[str],
) -> dict[str, Any]:
    old = summarize_roots(old_trace_roots, name=old_name)
    new = summarize_roots(new_trace_roots, name=new_name)
    old_records = old.get("records", {}) or {}
    new_records = new.get("records", {}) or {}
    common = sorted(set(old_records) & set(new_records))

    transition_counts: Counter[str] = Counter()
    timeout_cases: list[dict[str, Any]] = []
    improved_from_timeout: list[str] = []
    timeout_to_success: list[str] = []
    persistent_timeouts: list[str] = []
    no_submit_to_submit: list[str] = []
    submitted_timeout_with_dynamic: list[str] = []
    regressions_to_timeout: list[str] = []

    for task_id in common:
        old_record = old_records[task_id]
        new_record = new_records[task_id]
        old_bucket = _outcome_bucket(old_record)
        new_bucket = _outcome_bucket(new_record)
        transition_counts[f"{old_bucket}->{new_bucket}"] += 1

        if old_bucket == "timeout":
            row = {
                "task_id": task_id,
                "old_status": old_record.get("status", ""),
                "new_status": new_record.get("status", ""),
                "old_success": bool(old_record.get("success")),
                "new_success": bool(new_record.get("success")),
                "old_submit_count": int(old_record.get("submit_count", 0) or 0),
                "new_submit_count": int(new_record.get("submit_count", 0) or 0),
                "new_run_candidate_count": int(new_record.get("run_candidate_count", 0) or 0),
                "new_probe_runtime_frontier_count": int(new_record.get("probe_runtime_frontier_count", 0) or 0),
                "new_run_candidate_outcomes": new_record.get("run_candidate_outcomes", {}),
                "new_frontier_statuses": new_record.get("frontier_statuses", {}),
                "new_first_unreached_roles": new_record.get("first_unreached_roles", {}),
                "new_runtime_evidence_visible": bool(new_record.get("runtime_evidence_visible")),
                "new_frontier_visible": bool(new_record.get("frontier_visible")),
                "trace_dir": new_record.get("trace_dir", ""),
            }
            timeout_cases.append(row)
            if new_bucket != "timeout":
                improved_from_timeout.append(task_id)
            if new_bucket == "success":
                timeout_to_success.append(task_id)
            if new_bucket == "timeout":
                persistent_timeouts.append(task_id)
            if int(old_record.get("submit_count", 0) or 0) == 0 and int(new_record.get("submit_count", 0) or 0) > 0:
                no_submit_to_submit.append(task_id)
            if new_bucket == "timeout" and int(new_record.get("submit_count", 0) or 0) > 0 and int(new_record.get("dynamic_tool_count", 0) or 0) > 0:
                submitted_timeout_with_dynamic.append(task_id)

        if old_bucket != "timeout" and new_bucket == "timeout":
            regressions_to_timeout.append(task_id)

    old_timeout_count = sum(1 for record in old_records.values() if _outcome_bucket(record) == "timeout")
    new_timeout_count = sum(1 for record in new_records.values() if _outcome_bucket(record) == "timeout")
    common_old_timeout_count = sum(1 for task_id in common if _outcome_bucket(old_records[task_id]) == "timeout")
    common_new_timeout_count = sum(1 for task_id in common if _outcome_bucket(new_records[task_id]) == "timeout")

    return {
        "old": _compact_run_summary(old),
        "new": _compact_run_summary(new),
        "intersection": {
            "common_task_count": len(common),
            "old_only": len(set(old_records) - set(new_records)),
            "new_only": len(set(new_records) - set(old_records)),
            "old_timeout_count": old_timeout_count,
            "new_timeout_count": new_timeout_count,
            "common_old_timeout_count": common_old_timeout_count,
            "common_new_timeout_count": common_new_timeout_count,
            "common_timeout_delta": common_new_timeout_count - common_old_timeout_count,
        },
        "transition_counts": dict(transition_counts),
        "timeout_transition": {
            "old_timeout_common_cases": common_old_timeout_count,
            "improved_from_timeout_count": len(improved_from_timeout),
            "timeout_to_success_count": len(timeout_to_success),
            "persistent_timeout_count": len(persistent_timeouts),
            "no_submit_to_submit_count": len(no_submit_to_submit),
            "submitted_timeout_with_dynamic_count": len(submitted_timeout_with_dynamic),
            "regressions_to_timeout_count": len(regressions_to_timeout),
            "improved_from_timeout": improved_from_timeout,
            "timeout_to_success": timeout_to_success,
            "persistent_timeouts": persistent_timeouts,
            "no_submit_to_submit": no_submit_to_submit,
            "submitted_timeout_with_dynamic": submitted_timeout_with_dynamic,
            "regressions_to_timeout": regressions_to_timeout,
        },
        "old_timeout_cases_in_new": timeout_cases,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    old = payload["old"]
    new = payload["new"]
    intersection = payload["intersection"]
    timeout = payload["timeout_transition"]
    rows = [
        f"| metric | {old['name']} | {new['name']} |",
        "|---|---:|---:|",
        f"| traces | {old['trace_count']} | {new['trace_count']} |",
        f"| completed | {old['completed']} | {new['completed']} |",
        f"| success | {old['success']} | {new['success']} |",
        f"| crash/completed | {old['crash_completed_rate']:.3f} | {new['crash_completed_rate']:.3f} |",
        f"| budget_time | {old['budget_time_count']} | {new['budget_time_count']} |",
        f"| submitted timeout | {old['submitted_timeout_count']} | {new['submitted_timeout_count']} |",
        f"| no-submit timeout | {old['no_submit_timeout_count']} | {new['no_submit_timeout_count']} |",
        f"| dynamic traces | {old['dynamic_trace_count']} | {new['dynamic_trace_count']} |",
        f"| run_candidate traces | {old['run_candidate_trace_count']} | {new['run_candidate_trace_count']} |",
        f"| GDB frontier traces | {old['probe_runtime_frontier_trace_count']} | {new['probe_runtime_frontier_trace_count']} |",
    ]
    timeout_rows = [
        "| timeout transition metric | value |",
        "|---|---:|",
        f"| common tasks | {intersection['common_task_count']} |",
        f"| common timeout delta | {intersection['common_timeout_delta']} |",
        f"| old timeout common cases | {timeout['old_timeout_common_cases']} |",
        f"| improved from timeout | {timeout['improved_from_timeout_count']} |",
        f"| timeout → success | {timeout['timeout_to_success_count']} |",
        f"| persistent timeout | {timeout['persistent_timeout_count']} |",
        f"| no-submit timeout → submitted | {timeout['no_submit_to_submit_count']} |",
        f"| persistent submitted timeout with dynamic evidence | {timeout['submitted_timeout_with_dynamic_count']} |",
        f"| regressions to timeout | {timeout['regressions_to_timeout_count']} |",
    ]
    return "\n".join([
        "# CyberGym timeout transition comparison",
        "",
        *rows,
        "",
        "## Timeout transitions",
        "",
        *timeout_rows,
        "",
        "## Transition buckets",
        "",
        "```json",
        json.dumps(payload["transition_counts"], indent=2, sort_keys=True),
        "```",
        "",
        "## Task lists",
        "",
        "```json",
        json.dumps({
            "improved_from_timeout": timeout["improved_from_timeout"],
            "timeout_to_success": timeout["timeout_to_success"],
            "persistent_timeouts": timeout["persistent_timeouts"],
            "regressions_to_timeout": timeout["regressions_to_timeout"],
        }, indent=2, sort_keys=True),
        "```",
        "",
    ])


def _compact_run_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary.get(key)
        for key in (
            "name",
            "trace_count",
            "completed",
            "success",
            "crash_completed_rate",
            "status_breakdown",
            "budget_time_count",
            "submitted_timeout_count",
            "no_submit_timeout_count",
            "dynamic_trace_count",
            "run_candidate_trace_count",
            "probe_runtime_frontier_trace_count",
            "dynamic_after_submit_count",
            "runtime_evidence_visible_count",
            "frontier_visible_count",
            "run_candidate_outcomes",
            "frontier_statuses",
            "first_unreached_roles",
        )
    }


def _outcome_bucket(record: dict[str, Any]) -> str:
    if bool(record.get("success")):
        return "success"
    status = str(record.get("status") or "")
    if status == "running":
        return "running"
    if status == "budget_time":
        return "timeout"
    if status:
        return "other_done"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-name", required=True)
    parser.add_argument("--old-trace-root", action="append", required=True)
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--new-trace-root", action="append", required=True)
    parser.add_argument("--results-json", required=True)
    parser.add_argument("--results-md", required=True)
    args = parser.parse_args()

    payload = compare_timeout_transitions(
        old_name=args.old_name,
        old_trace_roots=args.old_trace_root,
        new_name=args.new_name,
        new_trace_roots=args.new_trace_root,
    )
    Path(args.results_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_json).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = render_markdown(payload)
    Path(args.results_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_md).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
