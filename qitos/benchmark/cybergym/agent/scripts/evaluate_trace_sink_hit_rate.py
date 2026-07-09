#!/usr/bin/env python3
"""Evaluate recorded sink candidates in traces against error-stack ground truth."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from offline_eval.error_stack import Candidate, evaluate_candidates, normalize_symbol, symbol_matches  # noqa: E402


@dataclass
class TraceCandidate:
    function: str
    confidence: float = 0.0
    category: str = ""
    source: str = ""
    step: int = 0
    evidence: str = ""


@dataclass
class TraceRecord:
    task_id: str
    trace_dir: str
    candidates: list[TraceCandidate] = field(default_factory=list)
    status: str = ""
    success: bool = False


def load_ground_truth(path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    target = Path(path)
    if target.suffix == ".jsonl":
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            task_id = str(row.get("task_id") or "")
            if task_id:
                rows[task_id] = row
    else:
        with target.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                task_id = str(row.get("task_id") or "")
                if task_id:
                    rows[task_id] = row
    return rows


def _task_id_from_trace(trace_dir: Path) -> str:
    manifest = trace_dir / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
            task_id = (
                data.get("summary", {})
                .get("task_meta", {})
                .get("task_id", "")
            )
            if task_id:
                return str(task_id)
        except Exception:
            pass
    match = re.search(r"_arvo_(\d+)_", trace_dir.name)
    if match:
        return f"arvo:{match.group(1)}"
    return trace_dir.name


def _trace_status(trace_dir: Path) -> tuple[str, bool]:
    manifest = trace_dir / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
            summary = data.get("summary", {})
            task_result = summary.get("task_result", {})
            return str(summary.get("stop_reason") or data.get("status") or ""), bool(task_result.get("success"))
        except Exception:
            pass
    text = ""
    tui = trace_dir / "tui.log"
    if tui.is_file():
        text = tui.read_text(encoding="utf-8", errors="replace")
    return ("done" if "DONE" in text else "running"), "VUL TRIGGERED" in text


def extract_trace_candidates(trace_dir: str | Path) -> TraceRecord:
    trace_path = Path(trace_dir)
    record = TraceRecord(task_id=_task_id_from_trace(trace_path), trace_dir=str(trace_path))
    record.status, record.success = _trace_status(trace_path)
    seen: set[str] = set()

    for response_path in sorted(trace_path.glob("agent_steps/step-*/model_response.json")):
        step = _step_number(response_path)
        try:
            data = json.loads(response_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for call in data.get("tool_calls") or []:
            fn = (call.get("function") or {}) if isinstance(call, dict) else {}
            if fn.get("name") != "record_sink_candidate":
                continue
            args = _loads_args(fn.get("arguments"))
            function = str(args.get("function") or "").strip()
            if not function:
                continue
            key = normalize_symbol(function)
            if key in seen:
                continue
            seen.add(key)
            record.candidates.append(TraceCandidate(
                function=function,
                confidence=_float(args.get("confidence")),
                category=str(args.get("category") or ""),
                source="model_response",
                step=step,
                evidence=str(args.get("evidence") or "")[:240],
            ))

    if not record.candidates:
        record.candidates.extend(_extract_candidates_from_tui(trace_path, seen))
    return record


def _step_number(path: Path) -> int:
    match = re.search(r"step-(\d+)", path.as_posix())
    return int(match.group(1)) if match else 0


def _loads_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _extract_candidates_from_tui(trace_path: Path, seen: set[str]) -> list[TraceCandidate]:
    tui = trace_path / "tui.log"
    if not tui.is_file():
        return []
    text = tui.read_text(encoding="utf-8", errors="replace")
    results: list[TraceCandidate] = []
    pattern = re.compile(r"Action\(name='record_sink_candidate'.*?args=\{(?P<args>.*?)\}", re.DOTALL)
    for match in pattern.finditer(text):
        args_text = match.group("args")
        func_match = re.search(r"'function':\\s*'([^']+)'", args_text) or re.search(r'"function":\\s*"([^"]+)"', args_text)
        if not func_match:
            continue
        function = func_match.group(1).strip()
        key = normalize_symbol(function)
        if key in seen:
            continue
        seen.add(key)
        conf_match = re.search(r"'confidence':\\s*([0-9.]+)", args_text) or re.search(r'"confidence":\\s*([0-9.]+)', args_text)
        results.append(TraceCandidate(
            function=function,
            confidence=_float(conf_match.group(1)) if conf_match else 0.0,
            source="tui_log",
        ))
    return results


def _gt_reports_for_eval(gt_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a minimal report-like object compatible with evaluate_candidates."""
    # evaluate_candidates expects ErrorStackReport objects.  The tiny adapter
    # below implements the frames/causal_frames surface using parsed GT rows.
    class ReportAdapter:
        def __init__(self, row: dict[str, Any]) -> None:
            self.row = row

        def frames(self, kind: str = "primary", *, project_only: bool = False) -> list[Any]:
            if kind != "primary":
                return []
            return [_FrameAdapter(item) for item in self.row.get("crash_path") or [] if item.get("function")]

        def causal_frames(self, *, project_only: bool = True) -> list[Any]:
            return [_FrameAdapter(item) for item in self.row.get("causal_frames") or [] if item.get("function")]

    class _FrameAdapter:
        def __init__(self, item: dict[str, Any]) -> None:
            self.function = str(item.get("function") or "")
            self.file = str(item.get("file") or "")
            self.line = int(item.get("line") or 0)

    return {task_id: ReportAdapter(row) for task_id, row in gt_rows.items()}


def _candidate_payloads(records: Iterable[TraceRecord]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        result[record.task_id] = [
            {
                "function": item.function,
                "confidence": item.confidence,
                "family": item.category,
                "role": "crash_site",
                "resolution_status": "recorded",
            }
            for item in record.candidates
        ]
    return result


def _subset_metrics(task_results: dict[str, Any], records: list[TraceRecord], *, cutoffs: tuple[int, ...] = (1, 3, 5)) -> dict[str, Any]:
    record_map = {record.task_id: record for record in records}

    def build(task_ids: list[str]) -> dict[str, Any]:
        values = [task_results[task_id] for task_id in task_ids if task_id in task_results]
        total = len(values)
        if not total:
            return {"tasks": 0}

        def recall(field_name: str, cutoff: int) -> float:
            hits = sum(
                item.get(field_name) is not None and int(item.get(field_name)) <= cutoff
                for item in values
            )
            return round(hits / total, 6)

        return {
            "tasks": total,
            "success_rate": round(
                sum(1 for task_id in task_ids if record_map.get(task_id) and record_map[task_id].success) / total,
                6,
            ),
            "average_candidates": round(sum(item.get("candidate_count", 0) for item in values) / total, 4),
            "exact_recall_at": {str(k): recall("exact_sink_rank", k) for k in cutoffs},
            "crash_path_recall_at": {str(k): recall("crash_path_rank", k) for k in cutoffs},
            "causal_coverage_at": {str(k): recall("causal_rank", k) for k in cutoffs},
        }

    all_ids = [record.task_id for record in records]
    completed_ids = [record.task_id for record in records if record.status != "running"]
    success_ids = [record.task_id for record in records if record.success]
    with_candidates_ids = [record.task_id for record in records if record.candidates]
    return {
        "all": build(all_ids),
        "completed": build(completed_ids),
        "success": build(success_ids),
        "with_candidates": build(with_candidates_ids),
    }


def _match_level(candidate: str, targets: list[str]) -> str:
    for target in targets:
        if normalize_symbol(candidate) == normalize_symbol(target):
            return "exact"
    for target in targets:
        if symbol_matches(candidate, target):
            return "suffix"
    for target in targets:
        c = normalize_symbol(candidate)
        t = normalize_symbol(target)
        if c and t and (c in t or t in c):
            return "substring"
    return "none"


def write_results_csv(path: str | Path, records: list[TraceRecord], gt_rows: dict[str, dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "task_id", "trace_dir", "status", "success", "ground_truth_sink",
            "sink_candidates", "n_candidates", "match_level", "exact_rank",
            "path_rank", "causal_rank",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = gt_rows.get(record.task_id, {})
            targets = [str(row.get("crash_site_function") or "")]
            path_targets = list(row.get("crash_path_functions") or [])
            causal_targets = list(row.get("causal_functions") or [])
            candidates = [item.function for item in record.candidates]
            best = "none"
            priority = {"none": 0, "substring": 1, "suffix": 2, "exact": 3}
            for candidate in candidates:
                level = _match_level(candidate, targets)
                if priority[level] > priority[best]:
                    best = level
            writer.writerow({
                "task_id": record.task_id,
                "trace_dir": record.trace_dir,
                "status": record.status,
                "success": record.success,
                "ground_truth_sink": targets[0],
                "sink_candidates": "|".join(candidates),
                "n_candidates": len(candidates),
                "match_level": best,
                "exact_rank": _first_rank(candidates, targets),
                "path_rank": _first_rank(candidates, path_targets),
                "causal_rank": _first_rank(candidates, causal_targets),
            })


def _first_rank(candidates: list[str], targets: list[str]) -> int | str:
    for index, candidate in enumerate(candidates, start=1):
        if _match_level(candidate, targets) != "none":
            return index
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, help="JSONL produced by build_error_stack_ground_truth.py")
    parser.add_argument("--trace-root", action="append", required=True, help="Trace root or individual trace dir; repeatable")
    parser.add_argument("--results-csv", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--stdout", choices=("compact", "full"), default="compact")
    args = parser.parse_args()

    gt_rows = load_ground_truth(args.ground_truth)
    trace_dirs: list[Path] = []
    for root_value in args.trace_root:
        root = Path(root_value)
        if (root / "manifest.json").is_file():
            trace_dirs.append(root)
        elif root.is_dir():
            trace_dirs.extend(sorted(path for path in root.iterdir() if path.is_dir()))
    records = [extract_trace_candidates(path) for path in trace_dirs]
    records = [record for record in records if record.task_id in gt_rows]

    reports = _gt_reports_for_eval(gt_rows)
    candidates = _candidate_payloads(records)
    summary = evaluate_candidates(
        {task_id: reports[task_id] for task_id in candidates if task_id in reports},
        candidates,
    ).to_dict()
    task_results = summary.get("task_results", {})
    summary.update({
        "trace_count": len(trace_dirs),
        "evaluated_traces": len(records),
        "tasks_with_candidates": sum(1 for record in records if record.candidates),
        "successful_traces": sum(1 for record in records if record.success),
        "status_breakdown": dict(Counter(record.status for record in records)),
        "subset_metrics": _subset_metrics(task_results, records),
    })
    write_results_csv(args.results_csv, records, gt_rows)
    Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_json).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.stdout == "full":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        compact = {key: value for key, value in summary.items() if key != "task_results"}
        print(json.dumps(compact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
