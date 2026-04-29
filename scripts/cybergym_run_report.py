#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


SUCCESS_REASON = "success"


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    stop_reason: str
    trace_id: str
    manifest_path: Path
    steps: int | None = None
    latency_seconds: float | None = None
    token_usage: int | None = None
    final_result: str | None = None

    @property
    def success(self) -> bool:
        return self.stop_reason == SUCCESS_REASON


@dataclass(frozen=True)
class RunReport:
    name: str
    path: Path
    tasks: dict[str, TaskResult]
    manifest_count: int

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def success_count(self) -> int:
        return sum(1 for result in self.tasks.values() if result.success)

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total if self.total else 0.0

    @property
    def stop_reasons(self) -> dict[str, int]:
        return dict(Counter(result.stop_reason for result in self.tasks.values()))


def _nested(mapping: dict[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _task_id_from_manifest(path: Path, obj: dict[str, Any]) -> str:
    for value in (
        _nested(obj, "summary", "task_meta", "task_id"),
        _nested(obj, "summary", "task_result", "task_id"),
        _nested(obj, "experiment_spec", "benchmark_metadata", "task_id"),
    ):
        if value:
            return str(value)
    name = path.parent.name
    marker = "_arvo_"
    if marker in name:
        return "arvo:" + name.split(marker, 1)[1].split("_", 1)[0]
    return ""


def _stop_reason_from_manifest(obj: dict[str, Any]) -> str:
    summary = obj.get("summary") if isinstance(obj.get("summary"), dict) else {}
    task_result = summary.get("task_result") if isinstance(summary.get("task_result"), dict) else {}
    if task_result.get("success") is True:
        return SUCCESS_REASON
    for value in (
        task_result.get("stop_reason"),
        summary.get("stop_reason"),
        obj.get("status"),
    ):
        if value:
            return str(value)
    return "unknown"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_task_result(manifest_path: Path) -> TaskResult | None:
    try:
        obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    summary = obj.get("summary") if isinstance(obj.get("summary"), dict) else {}
    task_id = _task_id_from_manifest(manifest_path, obj)
    if not task_id:
        return None
    token_usage = _optional_int(summary.get("token_usage"))
    if token_usage is None:
        token_usage = _optional_int(_nested(summary, "context", "tokens_total"))
    return TaskResult(
        task_id=task_id,
        stop_reason=_stop_reason_from_manifest(obj),
        trace_id=manifest_path.parent.name,
        manifest_path=manifest_path,
        steps=_optional_int(summary.get("steps")),
        latency_seconds=_optional_float(summary.get("latency_seconds")),
        token_usage=token_usage,
        final_result=str(summary.get("final_result")) if summary.get("final_result") else None,
    )


def _is_better_final_result(candidate: TaskResult, current: TaskResult) -> bool:
    if candidate.success != current.success:
        return candidate.success
    return candidate.manifest_path.stat().st_mtime >= current.manifest_path.stat().st_mtime


def collect_run_report(run_folder: Path | str) -> RunReport:
    root = Path(run_folder).expanduser().resolve()
    traces = root / "traces"
    tasks: dict[str, TaskResult] = {}
    manifest_count = 0
    if traces.is_dir():
        for manifest_path in sorted(traces.glob("*/manifest.json")):
            manifest_count += 1
            result = _load_task_result(manifest_path)
            if result is None:
                continue
            current = tasks.get(result.task_id)
            if current is None or _is_better_final_result(result, current):
                tasks[result.task_id] = result
    return RunReport(name=root.name, path=root, tasks=tasks, manifest_count=manifest_count)


def discover_run_folders(runs_root: Path | str) -> list[Path]:
    root = Path(runs_root).expanduser()
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and any((path / "traces").glob("*/manifest.json"))
    )


def _load_task_order(task_file: str | None) -> list[str]:
    if not task_file:
        return []
    path = Path(task_file).expanduser()
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _all_task_ids(reports: Sequence[RunReport], task_order: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for task_id in task_order:
        if task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)
    for report in reports:
        for task_id in sorted(report.tasks):
            if task_id not in seen:
                ordered.append(task_id)
                seen.add(task_id)
    return ordered


def _format_seconds(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}"


def _format_int(value: int | None) -> str:
    return "" if value is None else str(value)


def write_markdown_report(
    reports: Sequence[RunReport],
    *,
    output_path: Path,
    task_order: Sequence[str] = (),
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# CyberGym Run Report",
        "",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Runs: `{len(reports)}`",
        "",
        "## Summary",
        "",
        "| run | success | total | rate | manifests | stop reasons |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for report in reports:
        reasons = ", ".join(
            f"{reason}:{count}" for reason, count in sorted(report.stop_reasons.items())
        )
        lines.append(
            f"| `{report.name}` | {report.success_count} | {report.total} | "
            f"{report.success_rate * 100:.2f}% | {report.manifest_count} | {reasons} |"
        )

    all_tasks = _all_task_ids(reports, task_order)
    if reports and all_tasks:
        lines.extend(
            [
                "",
                "## Task Matrix",
                "",
                "Legend: `S` success, `-` missing, otherwise stop_reason.",
                "",
                "| task_id | " + " | ".join(f"`{report.name}`" for report in reports) + " |",
                "| --- | " + " | ".join("---" for _ in reports) + " |",
            ]
        )
        for task_id in all_tasks:
            cells = []
            for report in reports:
                result = report.tasks.get(task_id)
                if result is None:
                    cells.append("-")
                elif result.success:
                    cells.append("S")
                else:
                    cells.append(result.stop_reason)
            lines.append(f"| `{task_id}` | " + " | ".join(cells) + " |")

    lines.extend(["", "## Per-Run Details", ""])
    for report in reports:
        lines.extend(
            [
                f"### {report.name}",
                "",
                "| task_id | stop_reason | steps | latency_s | tokens | final_result | trace |",
                "| --- | --- | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for task_id in _all_task_ids([report], task_order):
            result = report.tasks.get(task_id)
            if result is None:
                lines.append(f"| `{task_id}` | missing |  |  |  |  |  |")
                continue
            lines.append(
                f"| `{task_id}` | {result.stop_reason} | {_format_int(result.steps)} | "
                f"{_format_seconds(result.latency_seconds)} | {_format_int(result.token_usage)} | "
                f"{result.final_result or ''} | `{result.trace_id}` |"
            )
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_task_csv(
    reports: Sequence[RunReport],
    *,
    output_path: Path,
    task_order: Sequence[str] = (),
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_tasks = _all_task_ids(reports, task_order)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "task_id",
                "run",
                "stop_reason",
                "success",
                "steps",
                "latency_seconds",
                "token_usage",
                "final_result",
                "trace_id",
                "manifest_path",
            ]
        )
        for task_id in all_tasks:
            for report in reports:
                result = report.tasks.get(task_id)
                if result is None:
                    writer.writerow([task_id, report.name, "missing", "false", "", "", "", "", "", ""])
                    continue
                writer.writerow(
                    [
                        task_id,
                        report.name,
                        result.stop_reason,
                        str(result.success).lower(),
                        result.steps or "",
                        result.latency_seconds or "",
                        result.token_usage or "",
                        result.final_result or "",
                        result.trace_id,
                        str(result.manifest_path),
                    ]
                )


def _default_output_path(runs_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return runs_root / "reports" / f"cybergym_run_report_{stamp}.md"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a single comparison report for CyberGym run folders."
    )
    parser.add_argument(
        "run_folders",
        nargs="*",
        help="Run folders to compare. If omitted, scan --runs-root for folders with traces.",
    )
    parser.add_argument(
        "--runs-root",
        default="runs/cybergym",
        help="Parent folder used when run_folders are omitted and for the default output path.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Markdown report path. Defaults to runs/cybergym/reports/cybergym_run_report_<timestamp>.md.",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        help="Optional task-level CSV output path.",
    )
    parser.add_argument(
        "--task-file",
        help="Optional task list used to order rows and show missing tasks.",
    )
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root).expanduser()
    run_folders = [Path(path).expanduser() for path in args.run_folders]
    if not run_folders:
        run_folders = discover_run_folders(runs_root)
    reports = [collect_run_report(path) for path in run_folders]
    reports = [report for report in reports if report.manifest_count > 0]
    if not reports:
        parser.error("no run folders with traces/*/manifest.json found")

    task_order = _load_task_order(args.task_file)
    output_path = Path(args.output).expanduser() if args.output else _default_output_path(runs_root)
    write_markdown_report(reports, output_path=output_path, task_order=task_order)
    print(f"Wrote markdown report: {output_path}")
    if args.csv_path:
        csv_path = Path(args.csv_path).expanduser()
        write_task_csv(reports, output_path=csv_path, task_order=task_order)
        print(f"Wrote task CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
