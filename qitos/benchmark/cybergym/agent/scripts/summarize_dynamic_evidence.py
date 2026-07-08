#!/usr/bin/env python3
"""Summarize dynamic execution evidence in CyberGym trace runs.

This script is intentionally trace-format tolerant.  Remote runs may sync full
trace directories, only ``tui.log``/``run.log``, or trace directories plus
runtime artifacts.  The summarizer therefore reads, in order of confidence:

1. structured tool calls from ``assembled_messages.json`` and
   ``agent_steps/step-*/model_response.json``;
2. structured runtime artifacts under ``.agent/runtime_evidence``;
3. compact observation lines in ``tui.log``.

The output is designed to answer the rollout question: after a submit miss, did
the agent actually use ``run_candidate`` / ``probe_runtime_frontier`` and did
those results become model-visible compact evidence?
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_trace_sink_hit_rate import (  # noqa: E402
    discover_trace_dirs,
    extract_trace_candidates,
)


DYNAMIC_TOOL_NAMES = {"run_candidate", "probe_runtime_frontier"}


@dataclass
class DynamicTraceSummary:
    task_id: str
    trace_dir: str
    status: str
    success: bool
    submit_count: int = 0
    first_submit_step: int = 0
    run_candidate_count: int = 0
    first_run_candidate_step: int = 0
    probe_runtime_frontier_count: int = 0
    first_probe_runtime_frontier_step: int = 0
    runtime_evidence_visible: bool = False
    frontier_visible: bool = False
    runtime_artifact_count: int = 0
    run_candidate_outcomes: dict[str, int] = field(default_factory=dict)
    frontier_statuses: dict[str, int] = field(default_factory=dict)
    first_unreached_roles: dict[str, int] = field(default_factory=dict)
    next_action_mentions: dict[str, int] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)

    @property
    def dynamic_tool_count(self) -> int:
        return self.run_candidate_count + self.probe_runtime_frontier_count

    @property
    def budget_time(self) -> bool:
        return self.status == "budget_time"

    @property
    def submitted_timeout(self) -> bool:
        return self.budget_time and self.submit_count > 0

    @property
    def no_submit_timeout(self) -> bool:
        return self.budget_time and self.submit_count == 0

    @property
    def dynamic_after_submit(self) -> bool:
        first_dynamic = min(
            [step for step in (self.first_run_candidate_step, self.first_probe_runtime_frontier_step) if step],
            default=0,
        )
        return bool(self.first_submit_step and first_dynamic and first_dynamic > self.first_submit_step)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update({
            "dynamic_tool_count": self.dynamic_tool_count,
            "budget_time": self.budget_time,
            "submitted_timeout": self.submitted_timeout,
            "no_submit_timeout": self.no_submit_timeout,
            "dynamic_after_submit": self.dynamic_after_submit,
        })
        return data


def summarize_trace(trace_dir: str | Path) -> DynamicTraceSummary:
    trace = Path(trace_dir)
    record = extract_trace_candidates(trace)
    action_stats = record.action_stats or {}
    tool_events = _tool_events(trace)
    tui_text = _read_text(trace / "tui.log")
    artifact_payloads = _runtime_artifact_payloads(trace)

    run_steps = [step for name, step in tool_events if name == "run_candidate"]
    frontier_steps = [step for name, step in tool_events if name == "probe_runtime_frontier"]
    submit_steps = [step for name, step in tool_events if name == "submit_poc"]

    run_outcomes: Counter[str] = Counter()
    frontier_statuses: Counter[str] = Counter()
    first_unreached: Counter[str] = Counter()
    evidence_refs: list[str] = []

    for payload in artifact_payloads:
        classification = payload.get("classification") if isinstance(payload.get("classification"), dict) else {}
        outcome = str(
            payload.get("outcome")
            or classification.get("outcome")
            or payload.get("status")
            or ""
        ).strip()
        if outcome:
            run_outcomes[outcome] += 1
        status = str(payload.get("frontier_status") or payload.get("status") or "").strip()
        if _looks_like_frontier_payload(payload) and status:
            frontier_statuses[status] += 1
        role = str(payload.get("first_unreached_role") or "").strip()
        if role:
            first_unreached[role] += 1
        ref = str(payload.get("evidence_ref") or "").strip()
        if ref:
            evidence_refs.append(ref)

    for outcome in re.findall(r"Runtime evidence:\s+outcome=([A-Za-z0-9_:-]+)", tui_text):
        run_outcomes[outcome] += 1
    for status in re.findall(r"(?:GDB frontier|Frontier probe|frontier probe):\s+status=([A-Za-z0-9_:-]+)", tui_text):
        frontier_statuses[status] += 1
    for role in re.findall(r"first_unreached=([A-Za-z0-9_:-]+)", tui_text):
        first_unreached[role] += 1
    evidence_refs.extend(_dedupe(re.findall(r"evidence=(\.agent/runtime_evidence/[^\s`]+)", tui_text)))

    next_actions = Counter()
    for action in (
        "run_candidate",
        "probe_runtime_frontier",
        "localize_field",
        "repair_carrier",
        "extract_harness_protocol",
        "verify_oracle_context",
        "change_seed",
        "switch_objective",
    ):
        count = tui_text.count(action)
        if count:
            next_actions[action] = count

    return DynamicTraceSummary(
        task_id=record.task_id,
        trace_dir=str(trace),
        status=record.status,
        success=record.success,
        submit_count=int(action_stats.get("submit_count", 0) or len(submit_steps)),
        first_submit_step=int(action_stats.get("first_submit_action", 0) or (min(submit_steps) if submit_steps else 0)),
        run_candidate_count=len(run_steps) or _fallback_action_count(tui_text, "run_candidate"),
        first_run_candidate_step=min(run_steps) if run_steps else _fallback_first_action_step(tui_text, "run_candidate"),
        probe_runtime_frontier_count=len(frontier_steps) or _fallback_action_count(tui_text, "probe_runtime_frontier"),
        first_probe_runtime_frontier_step=min(frontier_steps) if frontier_steps else _fallback_first_action_step(tui_text, "probe_runtime_frontier"),
        runtime_evidence_visible=("Runtime evidence:" in tui_text or "runtime_evidence" in tui_text),
        frontier_visible=("GDB frontier:" in tui_text or "Frontier probe:" in tui_text or "frontier probe:" in tui_text),
        runtime_artifact_count=len(artifact_payloads),
        run_candidate_outcomes=dict(run_outcomes),
        frontier_statuses=dict(frontier_statuses),
        first_unreached_roles=dict(first_unreached),
        next_action_mentions=dict(next_actions),
        evidence_refs=_dedupe(evidence_refs)[:12],
    )


def summarize_roots(trace_roots: Iterable[str | Path], *, name: str = "run") -> dict[str, Any]:
    traces = [summarize_trace(path) for path in discover_trace_dirs(trace_roots)]
    completed = [item for item in traces if item.status != "running"]
    success = [item for item in traces if item.success]
    submitted_timeouts = [item for item in traces if item.submitted_timeout]
    no_submit_timeouts = [item for item in traces if item.no_submit_timeout]
    dynamic_traces = [item for item in traces if item.dynamic_tool_count > 0]
    run_candidate_traces = [item for item in traces if item.run_candidate_count > 0]
    frontier_traces = [item for item in traces if item.probe_runtime_frontier_count > 0]
    no_trigger_dynamic = [item for item in submitted_timeouts if item.dynamic_tool_count > 0]
    dynamic_after_submit = [item for item in traces if item.dynamic_after_submit]

    return {
        "name": name,
        "trace_count": len(traces),
        "completed": len(completed),
        "success": len(success),
        "crash_completed_rate": round(len(success) / len(completed), 6) if completed else 0.0,
        "status_breakdown": dict(Counter(item.status for item in traces)),
        "budget_time_count": sum(1 for item in traces if item.budget_time),
        "submitted_timeout_count": len(submitted_timeouts),
        "no_submit_timeout_count": len(no_submit_timeouts),
        "dynamic_trace_count": len(dynamic_traces),
        "run_candidate_trace_count": len(run_candidate_traces),
        "probe_runtime_frontier_trace_count": len(frontier_traces),
        "dynamic_after_submit_count": len(dynamic_after_submit),
        "submitted_timeout_dynamic_count": len(no_trigger_dynamic),
        "runtime_evidence_visible_count": sum(1 for item in traces if item.runtime_evidence_visible),
        "frontier_visible_count": sum(1 for item in traces if item.frontier_visible),
        "runtime_artifact_trace_count": sum(1 for item in traces if item.runtime_artifact_count > 0),
        "run_candidate_outcomes": _sum_counters(item.run_candidate_outcomes for item in traces),
        "frontier_statuses": _sum_counters(item.frontier_statuses for item in traces),
        "first_unreached_roles": _sum_counters(item.first_unreached_roles for item in traces),
        "avg_submit_count": _avg(item.submit_count for item in traces),
        "avg_dynamic_tool_count": _avg(item.dynamic_tool_count for item in traces),
        "records": {item.task_id: item.to_dict() for item in traces},
    }


def render_markdown(summary: dict[str, Any]) -> str:
    rows = [
        "| metric | value |",
        "|---|---:|",
        f"| traces | {summary['trace_count']} |",
        f"| completed | {summary['completed']} |",
        f"| success | {summary['success']} |",
        f"| crash/completed | {summary['crash_completed_rate']:.3f} |",
        f"| budget_time | {summary['budget_time_count']} |",
        f"| timeout with submit | {summary['submitted_timeout_count']} |",
        f"| timeout without submit | {summary['no_submit_timeout_count']} |",
        f"| traces with run_candidate | {summary['run_candidate_trace_count']} |",
        f"| traces with probe_runtime_frontier | {summary['probe_runtime_frontier_trace_count']} |",
        f"| dynamic after submit | {summary['dynamic_after_submit_count']} |",
        f"| submitted timeouts with dynamic evidence | {summary['submitted_timeout_dynamic_count']} |",
        f"| runtime evidence visible | {summary['runtime_evidence_visible_count']} |",
        f"| frontier visible | {summary['frontier_visible_count']} |",
        f"| artifact traces | {summary['runtime_artifact_trace_count']} |",
        f"| avg submits | {summary['avg_submit_count']:.2f} |",
        f"| avg dynamic tool calls | {summary['avg_dynamic_tool_count']:.2f} |",
    ]
    return "\n".join([
        f"# Dynamic evidence summary: {summary['name']}",
        "",
        *rows,
        "",
        "## run_candidate outcomes",
        "",
        "```json",
        json.dumps(summary["run_candidate_outcomes"], indent=2, sort_keys=True),
        "```",
        "",
        "## probe_runtime_frontier statuses",
        "",
        "```json",
        json.dumps(summary["frontier_statuses"], indent=2, sort_keys=True),
        "```",
        "",
        "## first unreached roles",
        "",
        "```json",
        json.dumps(summary["first_unreached_roles"], indent=2, sort_keys=True),
        "```",
        "",
    ])


def _tool_events(trace: Path) -> list[tuple[str, int]]:
    events: list[tuple[str, int]] = []
    assembled = trace / "assembled_messages.json"
    if assembled.is_file():
        for message in _load_json_messages(assembled):
            step = int(message.get("_step_id") or 0) if isinstance(message, dict) else 0
            for call in (message.get("tool_calls") or []) if isinstance(message, dict) else []:
                name = _tool_call_name(call)
                if name:
                    events.append((name, step))

    for response_path in sorted(trace.glob("agent_steps/step-*/model_response.json")):
        step = _step_number(response_path)
        try:
            data = json.loads(response_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for call in data.get("tool_calls") or []:
            name = _tool_call_name(call)
            if name:
                events.append((name, step))

    if not events:
        events.extend(_tool_events_from_tui(trace / "tui.log"))
    return sorted(events, key=lambda item: item[1])


def _tool_events_from_tui(path: Path) -> list[tuple[str, int]]:
    text = _read_text(path)
    events: list[tuple[str, int]] = []
    step = 0
    for line in text.splitlines():
        if "ACTION  Action(name=" in line or "🚀  ACTION" in line or "┌  ACTION" in line:
            step += 1
            match = re.search(r"Action\(name=['\"]([^'\"]+)", line)
            if match:
                events.append((match.group(1), step))
    return events


def _runtime_artifact_payloads(trace: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result_path in sorted(trace.rglob("runtime_evidence/**/result.json")):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict):
            payloads.append(data)
    return payloads


def _looks_like_frontier_payload(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("first_unreached_role", "last_hit_role", "hit_probe_ids", "frontier_status"))


def _tool_call_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    fn = call.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(call.get("name") or "")


def _load_json_messages(path: Path) -> list[dict[str, Any]]:
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


def _step_number(path: Path) -> int:
    match = re.search(r"step-(\d+)", path.as_posix())
    return int(match.group(1)) if match else 0


def _fallback_action_count(text: str, action: str) -> int:
    return len(re.findall(rf"Action\(name=['\"]{re.escape(action)}['\"]", text))


def _fallback_first_action_step(text: str, action: str) -> int:
    step = 0
    for line in text.splitlines():
        if "ACTION  Action(name=" in line or "🚀  ACTION" in line or "┌  ACTION" in line:
            step += 1
            if re.search(rf"Action\(name=['\"]{re.escape(action)}['\"]", line):
                return step
    return 0


def _sum_counters(counters: Iterable[dict[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for counter in counters:
        total.update(counter)
    return dict(total)


def _avg(values: Iterable[int]) -> float:
    items = list(values)
    return round(mean(items), 4) if items else 0.0


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-root", action="append", required=True)
    parser.add_argument("--name", default="run")
    parser.add_argument("--results-json", required=True)
    parser.add_argument("--results-md", required=True)
    args = parser.parse_args()

    summary = summarize_roots(args.trace_root, name=args.name)
    Path(args.results_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_json).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = render_markdown(summary)
    Path(args.results_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_md).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
