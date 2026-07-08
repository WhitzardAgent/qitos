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
from offline_eval.sink_failure_taxonomy import classify_trace_failure  # noqa: E402


@dataclass
class TraceCandidate:
    function: str
    confidence: float = 0.0
    category: str = ""
    source: str = ""
    step: int = 0
    evidence: str = ""
    ranked_path_id: str = ""


@dataclass
class TraceRecord:
    task_id: str
    trace_dir: str
    candidates: list[TraceCandidate] = field(default_factory=list)
    status: str = ""
    success: bool = False
    action_stats: dict[str, Any] = field(default_factory=dict)
    context_stats: dict[str, Any] = field(default_factory=dict)


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
    if "stop=budget_time" in text:
        status = "budget_time"
    elif "DONE" in text:
        status = "done"
    elif "stop=success" in text:
        status = "success"
    else:
        status = "running"
    return status, "VUL TRIGGERED" in text or "stop=success" in text


def discover_trace_dirs(roots: Iterable[str | Path]) -> list[Path]:
    """Find trace directories under one or more roots.

    Batch exports are usually grouped as ``group/trace_dir`` while unit tests
    often pass a single trace directory.  A directory is considered a trace if
    it owns at least one canonical trace artifact.
    """
    found: dict[str, Path] = {}
    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        if _looks_like_trace_dir(root):
            found[root.as_posix()] = root
            continue
        if root.is_dir():
            for artifact in ("manifest.json", "tui.log", "assembled_messages.json"):
                for path in root.rglob(artifact):
                    found[path.parent.as_posix()] = path.parent
    return [found[key] for key in sorted(found)]


def _looks_like_trace_dir(path: Path) -> bool:
    return (
        (path / "manifest.json").is_file()
        or (path / "tui.log").is_file()
        or (path / "assembled_messages.json").is_file()
        or (path / "agent_steps").is_dir()
    )


def extract_trace_candidates(trace_dir: str | Path) -> TraceRecord:
    trace_path = Path(trace_dir)
    record = TraceRecord(task_id=_task_id_from_trace(trace_path), trace_dir=str(trace_path))
    record.status, record.success = _trace_status(trace_path)
    record.action_stats = trace_action_stats(trace_path)
    record.context_stats = trace_context_stats(trace_path)
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
                category=str(args.get("category") or args.get("candidate_role") or ""),
                ranked_path_id=str(args.get("ranked_path_id") or ""),
                source="model_response",
                step=step,
                evidence=str(args.get("evidence") or "")[:240],
            ))

    if not record.candidates:
        record.candidates.extend(extract_trace_candidates_from_tui(trace_path, seen))
    if not record.candidates:
        record.candidates.extend(extract_trace_candidates_from_assembled(trace_path, seen))
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


def extract_trace_candidates_from_tui(trace_path: Path, seen: set[str] | None = None) -> list[TraceCandidate]:
    tui = trace_path / "tui.log"
    if not tui.is_file():
        return []
    seen = seen if seen is not None else set()
    text = tui.read_text(encoding="utf-8", errors="replace")
    results: list[TraceCandidate] = []
    pattern = re.compile(r"Action\(name='record_sink_candidate'.*?args=\{(?P<args>.*?)\}", re.DOTALL)
    for match in pattern.finditer(text):
        args_text = match.group("args")
        func_match = re.search(r"'function':\s*'([^']+)'", args_text) or re.search(r'"function":\s*"([^"]+)"', args_text)
        if not func_match:
            continue
        function = func_match.group(1).strip()
        key = normalize_symbol(function)
        if key in seen:
            continue
        seen.add(key)
        conf_match = re.search(r"'confidence':\s*([0-9.]+)", args_text) or re.search(r'"confidence":\s*([0-9.]+)', args_text)
        role_match = re.search(r"'candidate_role':\s*'([^']+)'", args_text) or re.search(r'"candidate_role":\s*"([^"]+)"', args_text)
        path_match = re.search(r"'ranked_path_id':\s*'([^']+)'", args_text) or re.search(r'"ranked_path_id":\s*"([^"]+)"', args_text)
        results.append(TraceCandidate(
            function=function,
            confidence=_float(conf_match.group(1)) if conf_match else 0.0,
            category=role_match.group(1) if role_match else "",
            source="tui_log",
            ranked_path_id=path_match.group(1) if path_match else "",
            evidence=args_text[:240],
        ))
    return results


def extract_trace_candidates_from_assembled(trace_path: Path, seen: set[str] | None = None) -> list[TraceCandidate]:
    assembled = trace_path / "assembled_messages.json"
    if not assembled.is_file():
        return []
    seen = seen if seen is not None else set()
    tool_results: list[TraceCandidate] = []
    context_results: list[TraceCandidate] = []
    messages = _load_assembled_messages(assembled)
    for message in messages:
        step = int(message.get("_step_id") or 0) if isinstance(message, dict) else 0
        for call in (message.get("tool_calls") or []) if isinstance(message, dict) else []:
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
            tool_results.append(TraceCandidate(
                function=function,
                confidence=_float(args.get("confidence")),
                category=str(args.get("category") or args.get("candidate_role") or ""),
                source="assembled_tool_calls",
                step=step,
                ranked_path_id=str(args.get("ranked_path_id") or ""),
                evidence=str(args.get("evidence") or "")[:240],
            ))
    for message in messages:
        step = int(message.get("_step_id") or 0) if isinstance(message, dict) else 0
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        if not content:
            continue
        for match in re.finditer(r"-\s*Sink:\s*`([^`]+)`", content):
            function = _clean_context_function(match.group(1))
            key = normalize_symbol(function)
            if not function or key in seen:
                continue
            seen.add(key)
            context_results.append(TraceCandidate(
                function=function,
                confidence=0.35,
                category="context_confirmed_sink",
                source="assembled_context",
                step=step,
                evidence=match.group(0)[:240],
            ))
    return tool_results + context_results


def _clean_context_function(value: str) -> str:
    text = str(value or "").strip()
    text = text.split("@", 1)[0].strip()
    text = re.sub(r"\s+\([^)]*\)$", "", text).strip()
    return text


def _load_assembled_messages(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        messages = data.get("messages")
        if isinstance(messages, list):
            return [item for item in messages if isinstance(item, dict)]
        return [data]
    return []


def trace_action_stats(trace_path: str | Path) -> dict[str, Any]:
    trace = Path(trace_path)
    text = ""
    tui = trace / "tui.log"
    if tui.is_file():
        text = tui.read_text(encoding="utf-8", errors="replace")

    action_index = 0
    first_candidate = 0
    first_submit = 0
    first_useful_read = 0
    analyze_description = 0
    record_sink_candidate = 0
    read_count = 0
    grep_count = 0
    glob_count = 0
    first_high_role_grep_hit = 0
    first_static_aware_read = 0
    high_role_grep_hit_count = 0
    static_aware_read_count = 0
    static_read_actions_seen: set[int] = set()
    current_action_name = ""
    submit_render_count = 0
    submit_action_count = 0
    for line in text.splitlines():
        if "ACTION  Action(name=" in line or "🚀  ACTION" in line or "┌  ACTION" in line:
            action_index += 1
            action_match = re.search(r"Action\(name=['\"]([^'\"]+)", line)
            current_action_name = action_match.group(1) if action_match else ""
        if "Action(name='analyze_description'" in line:
            analyze_description += 1
        if "Action(name='record_sink_candidate'" in line:
            record_sink_candidate += 1
            if not first_candidate:
                first_candidate = action_index or record_sink_candidate
        if "Action(name='READ'" in line:
            read_count += 1
            if not first_useful_read:
                first_useful_read = action_index or read_count
        if "Action(name='GREP'" in line:
            grep_count += 1
        if "Action(name='GLOB'" in line:
            glob_count += 1
        if "[static lead role=" in line:
            high_role = any(
                f"role={role}" in line
                for role in ("crash_site", "causal_site", "parser_gate", "dispatch")
            )
            if current_action_name == "GREP" and high_role:
                high_role_grep_hit_count += 1
                if not first_high_role_grep_hit:
                    first_high_role_grep_hit = action_index or grep_count
            if current_action_name == "READ":
                if action_index not in static_read_actions_seen:
                    static_read_actions_seen.add(action_index)
                    static_aware_read_count += 1
                if not first_static_aware_read:
                    first_static_aware_read = action_index or read_count
        if "Static context (navigation leads; verify in source)" in line and current_action_name == "READ":
            if not first_static_aware_read:
                first_static_aware_read = action_index or read_count
        if "Action(name='submit_poc'" in line:
            submit_action_count += 1
            if not first_submit:
                first_submit = action_index or submit_action_count
        if "[submit_poc(" in line:
            submit_render_count += 1

    # assembled_messages is usually cleaner for tool-call counts when tui.log
    # truncates arguments; use it to backfill missing first-action information.
    assembled = trace / "assembled_messages.json"
    assembled_submit = 0
    assembled_candidate = 0
    assembled_analyze = 0
    if assembled.is_file():
        for message in _load_assembled_messages(assembled):
            step = int(message.get("_step_id") or 0)
            for call in message.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                if name == "submit_poc":
                    assembled_submit += 1
                    if not first_submit:
                        first_submit = step
                elif name == "record_sink_candidate":
                    assembled_candidate += 1
                    if not first_candidate:
                        first_candidate = step
                elif name == "analyze_description":
                    assembled_analyze += 1

    submit_count = submit_render_count or submit_action_count or assembled_submit
    return {
        "first_candidate_action": first_candidate,
        "first_submit_action": first_submit,
        "first_useful_read_step": first_useful_read,
        "first_high_role_grep_hit_step": first_high_role_grep_hit,
        "first_static_aware_read_step": first_static_aware_read,
        "high_role_grep_hit_count": high_role_grep_hit_count,
        "static_aware_read_count": static_aware_read_count,
        "submit_count": submit_count,
        "submit_action_count": submit_action_count or assembled_submit,
        "analyze_description_count": analyze_description or assembled_analyze,
        "record_sink_candidate_count": record_sink_candidate or assembled_candidate,
        "read_count": read_count,
        "grep_count": grep_count,
        "glob_count": glob_count,
        "done_count": text.count("DONE"),
        "vul_triggered_count": text.count("VUL TRIGGERED"),
        "budget_time_count": text.count("stop=budget_time"),
    }


def trace_context_stats(trace_path: str | Path) -> dict[str, Any]:
    trace = Path(trace_path)
    assembled = trace / "assembled_messages.json"
    messages = _load_assembled_messages(assembled) if assembled.is_file() else []
    headings = (
        "## Mission",
        "## Current Assessment",
        "## Vulnerability Path",
        "## Required Conditions",
        "## Experiments",
        "## Next Action",
    )
    context_count = 0
    six_section_count = 0
    old_marker_count = 0
    required_pending = 0
    submit_now = 0
    sink_context_count = 0
    path_not_reached_without_evidence = 0
    path_normalization_warning = 0
    for message in messages:
        content = str(message.get("content") or "")
        if not content:
            continue
        is_context = "<RUNTIME_CONTEXT>" in content or all(head in content for head in headings[:2])
        if is_context:
            context_count += 1
            old_marker_count += sum(content.count(marker) for marker in ("Foundation", "Allowed Tools", "<analysis", "</analysis", "<tool_call", "raw dict"))
        if all(head in content for head in headings):
            six_section_count += 1
        if "Pending: no PoC-relevant conditions" in content or "candidate conditions were filtered as non-actionable" in content:
            required_pending += 1
        if "path_not_reached" in content and not any(
            marker in content
            for marker in ("AddressSanitizer", "MemorySanitizer", "Crash type:", "VUL TRIGGERED")
        ):
            path_not_reached_without_evidence += 1
        if any(
            marker in content
            for marker in (
                "normalization_warnings",
                "path_direction_reversed",
                "partial_recovered_direction",
                "partial_duplicate_nodes",
                "partial_invalid_direction",
                "loop_detected",
            )
        ):
            path_normalization_warning += 1
        submit_now += content.count("**SUBMIT NOW**")
        sink_context_count += len(re.findall(r"-\s*Sink:\s*`[^`]+`", content))
    return {
        "context_count": context_count,
        "six_section_count": six_section_count,
        "old_marker_count": old_marker_count,
        "required_conditions_pending_count": required_pending,
        "path_not_reached_without_evidence_count": path_not_reached_without_evidence,
        "path_normalization_warning_count": path_normalization_warning,
        "submit_now_count": submit_now,
        "context_sink_count": sink_context_count,
    }


# Backward-compatible private alias used by older ad-hoc notebooks.
_extract_candidates_from_tui = extract_trace_candidates_from_tui


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
                "role": item.category if item.category in {"crash_site", "causal_site", "path_anchor", "dangerous_primitive"} else "crash_site",
                "ranked_path_id": item.ranked_path_id,
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


def _rank_value(task_eval: dict[str, Any] | None, key: str) -> int | str:
    if task_eval and task_eval.get(key) is not None:
        return int(task_eval[key])
    return ""


def write_results_csv(
    path: str | Path,
    records: list[TraceRecord],
    gt_rows: dict[str, dict[str, Any]],
    task_results: dict[str, Any] | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    task_results = task_results or {}
    with target.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "task_id", "trace_dir", "status", "success", "ground_truth_sink",
            "sink_candidates", "n_candidates", "match_level", "exact_rank",
            "path_rank", "causal_rank", "first_candidate_action",
            "first_submit_action", "first_useful_read_step", "submit_count",
            "first_high_role_grep_hit_step", "first_static_aware_read_step",
            "high_role_grep_hit_count", "static_aware_read_count",
            "analyze_description_count", "context_count", "six_section_count",
            "old_marker_count", "required_conditions_pending_count",
            "path_not_reached_without_evidence_count",
            "path_normalization_warning_count", "submit_now_count",
            "failure_bucket", "crash_family",
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
            task_eval = task_results.get(record.task_id, {})
            action_stats = record.action_stats or {}
            context_stats = record.context_stats or {}
            failure_bucket = classify_trace_failure(
                record,
                row,
                task_eval,
                action_stats,
                context_stats,
            )
            writer.writerow({
                "task_id": record.task_id,
                "trace_dir": record.trace_dir,
                "status": record.status,
                "success": record.success,
                "ground_truth_sink": targets[0],
                "sink_candidates": "|".join(candidates),
                "n_candidates": len(candidates),
                "match_level": best,
                "exact_rank": _rank_value(task_eval, "exact_sink_rank") or _first_rank(candidates, targets),
                "path_rank": _rank_value(task_eval, "crash_path_rank") or _first_rank(candidates, path_targets),
                "causal_rank": _rank_value(task_eval, "causal_rank") or _first_rank(candidates, causal_targets),
                "first_candidate_action": action_stats.get("first_candidate_action", 0),
                "first_submit_action": action_stats.get("first_submit_action", 0),
                "first_useful_read_step": action_stats.get("first_useful_read_step", 0),
                "first_high_role_grep_hit_step": action_stats.get("first_high_role_grep_hit_step", 0),
                "first_static_aware_read_step": action_stats.get("first_static_aware_read_step", 0),
                "high_role_grep_hit_count": action_stats.get("high_role_grep_hit_count", 0),
                "static_aware_read_count": action_stats.get("static_aware_read_count", 0),
                "submit_count": action_stats.get("submit_count", 0),
                "analyze_description_count": action_stats.get("analyze_description_count", 0),
                "context_count": context_stats.get("context_count", 0),
                "six_section_count": context_stats.get("six_section_count", 0),
                "old_marker_count": context_stats.get("old_marker_count", 0),
                "required_conditions_pending_count": context_stats.get("required_conditions_pending_count", 0),
                "path_not_reached_without_evidence_count": context_stats.get("path_not_reached_without_evidence_count", 0),
                "path_normalization_warning_count": context_stats.get("path_normalization_warning_count", 0),
                "submit_now_count": context_stats.get("submit_now_count", 0),
                "failure_bucket": failure_bucket,
                "crash_family": row.get("crash_family") or row.get("crash_type") or "",
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
    trace_dirs = discover_trace_dirs(args.trace_root)
    records = [extract_trace_candidates(path) for path in trace_dirs]
    records = [record for record in records if record.task_id in gt_rows]

    reports = _gt_reports_for_eval(gt_rows)
    candidates = _candidate_payloads(records)
    summary = evaluate_candidates(
        {task_id: reports[task_id] for task_id in candidates if task_id in reports},
        candidates,
    ).to_dict()
    task_results = summary.get("task_results", {})
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
    non_success_completed = [
        record for record in records
        if record.status != "running" and not record.success
    ]
    path_normalization_traces = sum(
        1 for record in records
        if int(record.context_stats.get("path_normalization_warning_count", 0) or 0) > 0
    )
    summary.update({
        "trace_count": len(trace_dirs),
        "evaluated_traces": len(records),
        "tasks_with_candidates": sum(1 for record in records if record.candidates),
        "successful_traces": sum(1 for record in records if record.success),
        "status_breakdown": dict(Counter(record.status for record in records)),
        "failure_buckets": dict(failure_buckets),
        "no_crash_unknown_rate": round(
            failure_buckets.get("no_crash_unknown", 0) / len(non_success_completed),
            6,
        ) if non_success_completed else 0.0,
        "path_normalization_warning_rate": round(
            path_normalization_traces / len(records),
            6,
        ) if records else 0.0,
        "action_stats": {
            "submit_count": sum(int(record.action_stats.get("submit_count", 0) or 0) for record in records),
            "analyze_description_count": sum(int(record.action_stats.get("analyze_description_count", 0) or 0) for record in records),
            "record_sink_candidate_count": sum(int(record.action_stats.get("record_sink_candidate_count", 0) or 0) for record in records),
            "high_role_grep_hit_count": sum(int(record.action_stats.get("high_role_grep_hit_count", 0) or 0) for record in records),
            "static_aware_read_count": sum(int(record.action_stats.get("static_aware_read_count", 0) or 0) for record in records),
            "candidate_after_static_read_count": sum(
                1
                for record in records
                if int(record.action_stats.get("first_static_aware_read_step", 0) or 0) > 0
                and int(record.action_stats.get("first_candidate_action", 0) or 0)
                > int(record.action_stats.get("first_static_aware_read_step", 0) or 0)
            ),
        },
        "context_stats": {
            "context_count": sum(int(record.context_stats.get("context_count", 0) or 0) for record in records),
            "six_section_count": sum(int(record.context_stats.get("six_section_count", 0) or 0) for record in records),
            "old_marker_count": sum(int(record.context_stats.get("old_marker_count", 0) or 0) for record in records),
            "required_conditions_pending_count": sum(int(record.context_stats.get("required_conditions_pending_count", 0) or 0) for record in records),
            "path_not_reached_without_evidence_count": sum(int(record.context_stats.get("path_not_reached_without_evidence_count", 0) or 0) for record in records),
            "path_normalization_warning_count": sum(int(record.context_stats.get("path_normalization_warning_count", 0) or 0) for record in records),
            "submit_now_count": sum(int(record.context_stats.get("submit_now_count", 0) or 0) for record in records),
        },
        "subset_metrics": _subset_metrics(task_results, records),
    })
    write_results_csv(args.results_csv, records, gt_rows, task_results)
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
