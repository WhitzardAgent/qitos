"""CyberGym benchmark runner."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qitos.core import BenchmarkRunResult, ExperimentSpec, RunSpec, Task
from qitos.engine.stop_criteria import FinalResultCriteria, MaxRuntimeCriteria
from qitos.engine.states import ContextConfig, RuntimeBudget
from qitos.kit.env.host_env import HostEnv
from qitos.render import ClaudeStyleHook
from qitos.trace import TraceWriter

from .adapter import task_slug
from .evaluator import CyberGymEvaluator
from .runtime import CyberGymRuntimeHook, prepare_task_dir
from .scorer import CyberGymScorer


def _make_tui_log_hook(log_file: str, theme: str = "research") -> ClaudeStyleHook:
    """Create a ClaudeStyleHook that writes TUI output to a per-task log file.

    The hook's console is a _TeeConsole that writes to both the terminal
    (for live monitoring) and a plain-text log file (for offline analysis).
    """
    return ClaudeStyleHook(log_file=log_file, theme=theme)


def make_trace_writer(
    *,
    trace_logdir: str | Path,
    trace_prefix: str,
    task_id: str,
    model_id: str,
) -> TraceWriter:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{trace_prefix}_{task_slug(task_id)}_{stamp}"
    return TraceWriter(
        output_dir=str(Path(trace_logdir).expanduser().resolve()),
        run_id=run_id,
        strict_validate=True,
        metadata={"model_id": model_id},
    )


def run_cybergym_agent_task(
    *,
    task_dir: str | Path,
    model_name: str,
    api_key: str,
    base_url: str,
    server: str,
    max_steps: int | None,
    max_runtime_seconds: float,
    trace_logdir: str | Path,
    trace_prefix: str = "qitos_cybergym",
    run_spec: RunSpec | None = None,
    experiment_spec: ExperimentSpec | None = None,
) -> dict[str, Any]:
    try:
        from .agent.adapter import CyberGymAdapter
        from .agent.cli import build_agent
        from .agent.stop_criteria import PoCVerificationCriteria
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "CyberGym agent package is not bundled in QitOS. "
            "Copy the cybergym_agent repository into `qitos/benchmark/cybergym/agent/` "
            "before running the CyberGym benchmark."
        ) from exc

    task_path = Path(task_dir).expanduser().resolve()
    adapter = CyberGymAdapter(server_url=server)
    # The benchmark run should be governed by wall-clock time rather than a
    # user-visible step cap. QitOS Engine still requires a finite internal step
    # budget, so use a high guardrail and rely on MaxRuntimeCriteria.
    internal_step_limit = int(max_steps or 1_000_000)
    task = adapter.from_task_dir(
        str(task_path),
        max_steps=internal_step_limit,
        max_runtime_seconds=max_runtime_seconds,
    )
    task_root = str(task.inputs.get("task_root") or task_path)
    source_root = str(task.inputs.get("source_root") or task_path)
    # Tools should operate from the prepared CyberGym task root so task files
    # such as submit.sh stay inside the workspace sandbox. The extracted source
    # root is still passed separately for repo indexing and source navigation.
    workspace_root = task_root

    agent = build_agent(
        model=model_name,
        workspace_root=workspace_root,
        task_root=task_root,
        server_url=server,
        max_steps=internal_step_limit,
        llm_config={"api_key": api_key, "base_url": base_url},
    )

    env = HostEnv(workspace_root=workspace_root)
    stop_criteria = [
        PoCVerificationCriteria(),
        FinalResultCriteria(),
        MaxRuntimeCriteria(max_runtime_seconds=max_runtime_seconds),
    ]
    context_config = ContextConfig(
        tool_result_max_chars=60000,
        conversation_max_rounds=0,
        loop_max_repeats=3,
    )
    trace_writer = make_trace_writer(
        trace_logdir=trace_logdir,
        trace_prefix=trace_prefix,
        task_id=task.id,
        model_id=model_name,
    )

    # Per-task TUI log: saves the same STEP/finish/tool_calls/ctx_used output
    # that appears in the terminal, for offline trajectory analysis.
    tui_log_file = str(Path(str(trace_writer.run_dir)) / "tui.log")

    result = agent.run(
        task=task,
        return_state=True,
        env=env,
        stop_criteria=stop_criteria,
        engine_kwargs={
            "budget": RuntimeBudget(
                max_steps=internal_step_limit,
                max_runtime_seconds=float(max_runtime_seconds),
            )
        },
        workspace=workspace_root,
        context_config=context_config,
        trace=trace_writer,
        render_hooks=[_make_tui_log_hook(tui_log_file, theme="research")],
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        description=task.inputs.get("description", ""),
        task_id=task.inputs.get("task_id", ""),
        agent_id=task.inputs.get("agent_id", ""),
        checksum=task.inputs.get("checksum", ""),
        server_url=task.inputs.get("server_url", server),
        error_txt=task.inputs.get("error_txt", ""),
        patch_diff=task.inputs.get("patch_diff", ""),
        task_root=task.inputs.get("task_root", task_root),
        source_root=source_root,
        repo_dir=source_root or task.inputs.get("repo_dir", ""),
        trace_run_dir=str(trace_writer.run_dir),
    )

    return {
        "task_id": task.id,
        "task_dir": str(task_path),
        "trace_run_dir": str(trace_writer.run_dir),
        "stop_reason": result.state.stop_reason,
        "final_result": result.state.final_result,
        "step_count": result.step_count,
        "task_result": result.task_result.to_dict() if result.task_result is not None else None,
    }


def run_cybergym_task(
    *, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec
) -> BenchmarkRunResult:
    started = time.time()
    effective_spec = RunSpec.from_value(run_spec)
    effective_spec.benchmark_name = effective_spec.benchmark_name or "cybergym"
    effective_spec.benchmark_split = effective_spec.benchmark_split or str(
        task.inputs.get("difficulty") or "level1"
    )
    effective_spec.toolset_name = effective_spec.toolset_name or "cybergym_agent"
    effective_spec.metadata = {
        **dict(effective_spec.metadata or {}),
        "recipe": "cybergym_agent",
    }

    environment = dict(effective_spec.environment or {})
    task_id = str(task.inputs.get("task_id") or task.id)
    difficulty = str(task.inputs.get("difficulty") or effective_spec.benchmark_split or "level1")
    workspace = Path(str(environment.get("workspace") or "runs/cybergym/workspace"))
    task_dir = workspace / task_slug(task_id)
    data_dir = str(environment.get("data_dir") or "")
    server = str(environment.get("server") or "")
    base_url = str(environment.get("base_url") or "")
    trace_logdir = str(environment.get("trace_logdir") or "runs/cybergym/traces")
    api_key = str(
        environment.get("api_key")
        or os.getenv("OPENAI_API_KEY", "")
        or os.getenv("QITOS_API_KEY", "")
        or os.getenv("CYBERGYM_CLAUDE_AUTH_TOKEN", "")
    )
    max_steps_raw = (effective_spec.metadata or {}).get("max_steps", task.budget.max_steps)
    max_steps = int(max_steps_raw) if max_steps_raw is not None else None
    max_runtime_seconds = float(
        (effective_spec.metadata or {}).get(
            "max_runtime_seconds",
            task.budget.max_runtime_seconds or 3600,
        )
    )

    if not data_dir:
        raise ValueError("CyberGym run requires run_spec.environment['data_dir']")
    if not server:
        raise ValueError("CyberGym run requires run_spec.environment['server']")
    if not base_url:
        raise ValueError("CyberGym run requires run_spec.environment['base_url']")
    if not api_key:
        raise ValueError("CyberGym run requires api_key or OPENAI_API_KEY/QITOS_API_KEY")

    prepare_task_dir(
        task_id=task_id,
        out_dir=task_dir,
        data_dir=data_dir,
        server=server,
        difficulty=difficulty,
    )

    prepared = CyberGymRuntimeHook().prepare(
        task=task,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
    )
    execution = run_cybergym_agent_task(
        task_dir=task_dir,
        model_name=str(effective_spec.model_name or ""),
        api_key=api_key,
        base_url=base_url,
        server=server,
        max_steps=max_steps,
        max_runtime_seconds=max_runtime_seconds,
        trace_logdir=trace_logdir,
        trace_prefix=str(environment.get("trace_prefix") or "qitos_cybergym"),
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
    )
    task_result = execution.get("task_result") or {}
    base_result = BenchmarkRunResult(
        task_id=task_id,
        benchmark="cybergym",
        split=difficulty,
        prediction=execution.get("final_result"),
        success=bool(task_result.get("success", False)),
        stop_reason=str(execution.get("stop_reason") or "unknown"),
        steps=int(execution.get("step_count") or 0),
        latency_seconds=float(time.time() - started),
        token_usage=int((task_result.get("metrics") or {}).get("token_usage", 0)),
        cost=0.0,
        trace_run_dir=str(execution.get("trace_run_dir") or ""),
        run_spec_ref=effective_spec.fingerprint(),
        metadata={"execution": execution},
    )
    evaluation = CyberGymEvaluator().evaluate(
        prepared=prepared,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
        execution=execution,
    )
    return CyberGymScorer().score(
        prepared=prepared,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
        execution=execution,
        evaluation=evaluation,
        base_result=base_result,
    )
