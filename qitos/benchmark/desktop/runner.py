"""Built-in runner for the official desktop starter benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qitos.core import BenchmarkRunResult, ExperimentSpec, RunSpec, Task, ToolResult
from qitos.recipes.desktop import build_benchmark_result, execute_desktop_task


DESKTOP_FAILURE_TAGS = (
    "perception_failure",
    "grounding_failure",
    "planning_failure",
    "action_selection_failure",
    "execution_environment_failure",
    "stop_completion_failure",
)


def _classify_failure(run: Any) -> list[str]:
    state = getattr(run, "state", None)
    records = list(getattr(run, "records", []) or [])
    tags: list[str] = []
    if state is not None:
        tags.extend(
            str(x)
            for x in (getattr(state, "failure_tags", []) or [])
            if str(x).strip()
        )
    for record in records:
        diagnostics = getattr(record, "parser_diagnostics", {}) or {}
        if diagnostics:
            tags.append("planning_failure")
        for result in list(getattr(record, "action_results", []) or []):
            tool_result = ToolResult.from_value(result)
            status = str(tool_result.status or "")
            category = str(
                (tool_result.metadata or {}).get("error_category") or ""
            ).strip()
            if category in {"validation_error", "permission_ask", "permission_denied"}:
                tags.append("execution_environment_failure")
            if status == "error":
                tags.append("action_selection_failure")
    seen: list[str] = []
    for tag in tags:
        if tag in DESKTOP_FAILURE_TAGS and tag not in seen:
            seen.append(tag)
    return seen


def run_desktop_starter_task(
    *, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec
) -> BenchmarkRunResult:
    workspace = Path("./playground/desktop_benchmark") / str(task.id)
    workspace.mkdir(parents=True, exist_ok=True)
    execution = execute_desktop_task(
        task=task,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        workspace=workspace,
        smoke=bool((run_spec.metadata or {}).get("desktop_smoke", False)),
        render=False,
        trace=True,
        trace_logdir=str((run_spec.environment or {}).get("trace_logdir") or "./runs"),
        max_steps=int(task.budget.max_steps or 8),
    )
    row = build_benchmark_result(execution, benchmark_name="desktop-starter")
    failure_tags = _classify_failure(execution.result)
    row.metadata = {
        **dict(row.metadata or {}),
        "failure_tags": failure_tags,
        "grounding_failure_tags": [
            tag for tag in failure_tags if tag == "grounding_failure"
        ],
        "parser_failure_tags": [
            tag for tag in failure_tags if tag == "planning_failure"
        ],
    }
    return row
