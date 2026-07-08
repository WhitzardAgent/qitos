#!/usr/bin/env python3
"""Compare two CyberGym trace runs using sink/path recall and trace behavior."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from offline_eval.sink_failure_taxonomy import classify_trace_failure  # noqa: E402
from scripts.evaluate_trace_sink_hit_rate import (  # noqa: E402
    _candidate_payloads,
    _gt_reports_for_eval,
    _subset_metrics,
    discover_trace_dirs,
    extract_trace_candidates,
    load_ground_truth,
)
from offline_eval.error_stack import evaluate_candidates  # noqa: E402


def summarize_run(name: str, trace_roots: list[str], gt_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    trace_dirs = discover_trace_dirs(trace_roots)
    records = [extract_trace_candidates(path) for path in trace_dirs]
    records = [record for record in records if record.task_id in gt_rows]
    reports = _gt_reports_for_eval(gt_rows)
    payloads = _candidate_payloads(records)
    summary = evaluate_candidates(
        {task_id: reports[task_id] for task_id in payloads if task_id in reports},
        payloads,
    ).to_dict()
    task_results = summary.get("task_results", {})
    completed = [record for record in records if record.status != "running"]
    success = [record for record in records if record.success]
    failure_buckets = Counter(
        classify_trace_failure(
            record,
            gt_rows.get(record.task_id, {}),
            task_results.get(record.task_id, {}),
            record.action_stats,
            record.context_stats,
        )
        for record in records
    )

    def avg_action(field: str, subset: list[Any] | None = None) -> float:
        values = [int((record.action_stats or {}).get(field, 0) or 0) for record in (subset or records)]
        return round(mean(values), 4) if values else 0.0

    context_total = sum(int((record.context_stats or {}).get("context_count", 0) or 0) for record in records)
    six_total = sum(int((record.context_stats or {}).get("six_section_count", 0) or 0) for record in records)
    old_total = sum(int((record.context_stats or {}).get("old_marker_count", 0) or 0) for record in records)
    path_norm_traces = sum(
        1 for record in records
        if int((record.context_stats or {}).get("path_normalization_warning_count", 0) or 0) > 0
    )
    non_success_completed = [record for record in completed if not record.success]

    return {
        "name": name,
        "trace_count": len(trace_dirs),
        "evaluated_traces": len(records),
        "completed": len(completed),
        "success": len(success),
        "crash_completed_rate": round(len(success) / len(completed), 6) if completed else 0.0,
        "crash_all_started_rate": round(len(success) / len(records), 6) if records else 0.0,
        "tasks_with_candidates": sum(1 for record in records if record.candidates),
        "average_candidates": summary.get("average_candidates", 0.0),
        "exact_recall_at": summary.get("exact_recall_at", {}),
        "crash_path_recall_at": summary.get("crash_path_recall_at", {}),
        "causal_coverage_at": summary.get("causal_coverage_at", {}),
        "subset_metrics": _subset_metrics(task_results, records),
        "failure_buckets": dict(failure_buckets),
        "no_crash_unknown_rate": round(
            failure_buckets.get("no_crash_unknown", 0) / len(non_success_completed),
            6,
        ) if non_success_completed else 0.0,
        "path_normalization_warning_rate": round(path_norm_traces / len(records), 6) if records else 0.0,
        "status_breakdown": dict(Counter(record.status for record in records)),
        "avg_first_candidate_action": avg_action("first_candidate_action"),
        "avg_first_submit_action": avg_action("first_submit_action"),
        "avg_submit_count": avg_action("submit_count"),
        "avg_submit_count_success": avg_action("submit_count", success),
        "avg_submit_count_non_success_completed": avg_action(
            "submit_count",
            [record for record in completed if not record.success],
        ),
        "observation_audit": {
            "context_count": context_total,
            "six_section_count": six_total,
            "six_section_rate": round(six_total / context_total, 6) if context_total else 0.0,
            "old_marker_count": old_total,
        },
        "task_results": task_results,
        "records": {
            record.task_id: {
                "trace_dir": record.trace_dir,
                "status": record.status,
                "success": record.success,
                "candidate_count": len(record.candidates),
                "submit_count": int((record.action_stats or {}).get("submit_count", 0) or 0),
                "failure_bucket": classify_trace_failure(
                    record,
                    gt_rows.get(record.task_id, {}),
                    task_results.get(record.task_id, {}),
                    record.action_stats,
                    record.context_stats,
                ),
            }
            for record in records
        },
    }


def compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_tasks = set((left.get("records") or {}).keys())
    right_tasks = set((right.get("records") or {}).keys())
    common = sorted(left_tasks & right_tasks)
    return {
        "common_task_count": len(common),
        "left_only": len(left_tasks - right_tasks),
        "right_only": len(right_tasks - left_tasks),
        "common_success": {
            left["name"]: sum(1 for task in common if left["records"][task]["success"]),
            right["name"]: sum(1 for task in common if right["records"][task]["success"]),
        },
        "common_completed": {
            left["name"]: sum(1 for task in common if left["records"][task]["status"] != "running"),
            right["name"]: sum(1 for task in common if right["records"][task]["status"] != "running"),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    left = payload["left"]
    right = payload["right"]
    rows = [
        "| metric | " + left["name"] + " | " + right["name"] + " |",
        "|---|---:|---:|",
        f"| evaluated traces | {left['evaluated_traces']} | {right['evaluated_traces']} |",
        f"| completed | {left['completed']} | {right['completed']} |",
        f"| crash/completed | {left['crash_completed_rate']:.3f} | {right['crash_completed_rate']:.3f} |",
        f"| crash/all_started | {left['crash_all_started_rate']:.3f} | {right['crash_all_started_rate']:.3f} |",
        f"| ExactSinkRecall@5 | {float(left['exact_recall_at'].get('5', 0.0)):.3f} | {float(right['exact_recall_at'].get('5', 0.0)):.3f} |",
        f"| CrashPathRecall@5 | {float(left['crash_path_recall_at'].get('5', 0.0)):.3f} | {float(right['crash_path_recall_at'].get('5', 0.0)):.3f} |",
        f"| CausalCoverage@5 | {float(left['causal_coverage_at'].get('5', 0.0)):.3f} | {float(right['causal_coverage_at'].get('5', 0.0)):.3f} |",
        f"| avg first candidate | {left['avg_first_candidate_action']:.2f} | {right['avg_first_candidate_action']:.2f} |",
        f"| avg first submit | {left['avg_first_submit_action']:.2f} | {right['avg_first_submit_action']:.2f} |",
        f"| avg submits | {left['avg_submit_count']:.2f} | {right['avg_submit_count']:.2f} |",
        f"| no_crash_unknown_rate | {left['no_crash_unknown_rate']:.3f} | {right['no_crash_unknown_rate']:.3f} |",
        f"| path_normalization_warning_rate | {left['path_normalization_warning_rate']:.3f} | {right['path_normalization_warning_rate']:.3f} |",
        f"| six-section rate | {left['observation_audit']['six_section_rate']:.3f} | {right['observation_audit']['six_section_rate']:.3f} |",
    ]
    return "\n".join([
        "# CyberGym run comparison",
        "",
        *rows,
        "",
        "## Failure buckets",
        "",
        f"- {left['name']}: `{json.dumps(left['failure_buckets'], sort_keys=True)}`",
        f"- {right['name']}: `{json.dumps(right['failure_buckets'], sort_keys=True)}`",
        "",
        "## Common task intersection",
        "",
        "```json",
        json.dumps(payload["intersection"], indent=2, sort_keys=True),
        "```",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--left-name", required=True)
    parser.add_argument("--left-trace-root", action="append", required=True)
    parser.add_argument("--right-name", required=True)
    parser.add_argument("--right-trace-root", action="append", required=True)
    parser.add_argument("--results-json", required=True)
    parser.add_argument("--results-md", required=True)
    args = parser.parse_args()

    gt_rows = load_ground_truth(args.ground_truth)
    left = summarize_run(args.left_name, args.left_trace_root, gt_rows)
    right = summarize_run(args.right_name, args.right_trace_root, gt_rows)
    payload = {"left": left, "right": right, "intersection": compare(left, right)}
    Path(args.results_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_json).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.results_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_md).write_text(render_markdown(payload), encoding="utf-8")
    print(render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
