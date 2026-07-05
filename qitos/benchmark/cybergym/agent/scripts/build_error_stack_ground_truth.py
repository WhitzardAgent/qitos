#!/usr/bin/env python3
"""Build sink ground truth from offline CyberGym ``error.txt`` files.

The generated files are evaluation labels only.  They are derived from
sanitizer stacks and must not be copied into task workspaces, runtime state,
observations, or prompts.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from offline_eval.error_stack import (  # noqa: E402
    ErrorStackReport,
    StackFrame,
    build_project_manifest,
    parse_error_file,
)


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _frame_dict(frame: StackFrame | None) -> dict[str, Any]:
    if frame is None:
        return {}
    return {
        "function": frame.function,
        "normalized_function": frame.normalized_function,
        "file": frame.file,
        "line": frame.line,
        "column": frame.column,
        "stack_kind": frame.stack_kind,
    }


def _frame_list(frames: list[StackFrame], limit: int) -> list[dict[str, Any]]:
    return [_frame_dict(frame) for frame in frames[:limit]]


def _dedup_functions(frames: list[StackFrame], limit: int = 24) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for frame in frames:
        key = frame.normalized_function
        if key and key not in seen:
            seen.add(key)
            values.append(frame.function)
        if len(values) >= limit:
            break
    return values


def build_ground_truth(
    *,
    tasks_json: str | Path,
    error_root: str | Path,
    split: str = "all",
) -> list[dict[str, Any]]:
    tasks = _load_json(tasks_json)
    if not isinstance(tasks, list):
        raise ValueError("tasks_json must contain a list")
    manifest = build_project_manifest(tasks, error_root)
    task_meta = {str(item.get("task_id") or ""): item for item in tasks if item.get("task_id")}
    root = Path(error_root)
    rows: list[dict[str, Any]] = []

    for entry in manifest:
        if split != "all" and str(entry.get("split") or "") != split:
            continue
        task_id = str(entry.get("task_id") or "")
        path = root / str(entry.get("error_path") or "")
        if not task_id or not path.is_file():
            continue
        report: ErrorStackReport = parse_error_file(path)
        primary = report.frames("primary", project_only=True)
        causal = report.causal_frames(project_only=True)
        crash_site = primary[0] if primary else None
        meta = task_meta.get(task_id, {})
        issue_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
        diagnostics = list(report.diagnostics)
        if not crash_site:
            diagnostics.append("crash_site_missing")
        rows.append({
            "schema_version": "error_stack_sinks_v1",
            "task_id": task_id,
            "issue_id": issue_id,
            "project_name": str(meta.get("project_name") or entry.get("project_name") or ""),
            "project_language": str(meta.get("project_language") or entry.get("project_language") or ""),
            "split": str(entry.get("split") or ""),
            "error_path": str(entry.get("error_path") or ""),
            "crash_type": report.crash_type,
            "access_mode": report.access_mode,
            "access_size": report.access_size,
            "crash_site": _frame_dict(crash_site),
            "crash_site_function": crash_site.function if crash_site else "",
            "crash_site_file": crash_site.file if crash_site else "",
            "crash_site_line": crash_site.line if crash_site else 0,
            "crash_path": _frame_list(primary, 12),
            "crash_path_functions": _dedup_functions(primary, 12),
            "causal_frames": _frame_list(causal, 12),
            "causal_functions": _dedup_functions(causal, 12),
            "diagnostics": diagnostics,
        })
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_id", "issue_id", "project_name", "project_language", "split",
        "crash_type", "access_mode", "access_size",
        "crash_site_function", "crash_site_file", "crash_site_line",
        "crash_path_functions", "causal_functions", "diagnostics",
        "error_path",
    ]
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: "|".join(str(item) for item in row.get(key, []))
                if key in {"crash_path_functions", "causal_functions", "diagnostics"}
                else row.get(key, "")
                for key in fieldnames
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-json", required=True)
    parser.add_argument("--error-root", required=True)
    parser.add_argument("--jsonl-out", required=True)
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--split", choices=("all", "train", "dev", "test"), default="all")
    args = parser.parse_args()

    rows = build_ground_truth(tasks_json=args.tasks_json, error_root=args.error_root, split=args.split)
    write_jsonl(args.jsonl_out, rows)
    write_csv(args.csv_out, rows)
    missing = sum(1 for row in rows if not row.get("crash_site_function"))
    print(json.dumps({
        "rows": len(rows),
        "missing_crash_site": missing,
        "jsonl_out": str(args.jsonl_out),
        "csv_out": str(args.csv_out),
        "split": args.split,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
