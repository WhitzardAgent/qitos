#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from qitos.benchmark.cybergym.adapter import load_cybergym_tasks
from qitos.benchmark.cybergym.runner import run_cybergym_task
from qitos.recipes.benchmarks._shared import (
    build_example_specs,
    execute_example_jobs,
    print_benchmark_summary,
)


def _load_task_ids(data_dir: Path, limit: int, start_index: int = 0) -> list[str]:
    arvo_root = data_dir / "arvo"
    task_dirs = sorted((p for p in arvo_root.iterdir() if p.is_dir()), key=lambda p: int(p.name))
    selected = task_dirs[int(start_index) :]
    if int(limit) > 0:
        selected = selected[: int(limit)]
    return [f"arvo:{p.name}" for p in selected]


def _load_task_ids_from_file(path: Path, limit: int) -> list[str]:
    items = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if int(limit) > 0:
        items = items[: int(limit)]
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a batch of CyberGym tasks with QitOS.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--difficulty", default="level1", choices=["level0", "level1", "level2", "level3"])
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=os.getenv("CYBERGYM_CLAUDE_AUTH_TOKEN", ""))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--task-file", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=1_000_000)
    parser.add_argument("--max-runtime-seconds", type=float, default=180.0)
    parser.add_argument("--trace-prefix", default="qitos_cybergym_batch")
    parser.add_argument("--output-jsonl", default="")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if not str(args.api_key).strip():
        raise SystemExit("api key is required")

    out_root = Path(args.out_root).expanduser().resolve()
    traces = out_root / "traces"
    workspace = out_root / "workspace"
    traces.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    data_dir = Path(args.data_dir).expanduser().resolve()
    task_file = str(args.task_file).strip()
    if task_file:
        task_ids = _load_task_ids_from_file(Path(task_file).expanduser().resolve(), limit=int(args.limit))
    else:
        task_ids = _load_task_ids(data_dir, limit=int(args.limit), start_index=int(args.start_index))
    tasks = load_cybergym_tasks(task_ids=task_ids, difficulty=args.difficulty)
    jobs = [{"task": task, "job_key": task.id} for task in tasks]

    run_spec, experiment_spec = build_example_specs(
        benchmark="cybergym",
        split=args.difficulty,
        model_name=str(args.model_name),
        trace_logdir=str(traces),
        parser_name="JsonDecisionParser",
        toolset_name="cybergym_agent",
        limit=len(jobs),
        workspace=str(workspace),
        metadata={
            "recipe": "cybergym_agent_batch",
            "max_steps": int(args.max_steps),
            "max_runtime_seconds": float(args.max_runtime_seconds),
        },
    )
    run_spec.environment = dict(run_spec.environment or {})
    run_spec.environment.update(
        {
            "data_dir": str(data_dir),
            "server": str(args.server),
            "base_url": str(args.base_url),
            "api_key": str(args.api_key),
            "trace_logdir": str(traces),
            "workspace": str(workspace),
            "trace_prefix": str(args.trace_prefix),
        }
    )
    output_path = (
        Path(args.output_jsonl).expanduser().resolve()
        if str(args.output_jsonl).strip()
        else out_root / f"cybergym_{args.difficulty}_first{len(jobs)}_conc{int(args.concurrency)}.jsonl"
    )

    rows = execute_example_jobs(
        jobs=jobs,
        runner=lambda **kwargs: run_cybergym_task(
            task=kwargs["task"],
            run_spec=kwargs["run_spec"],
            experiment_spec=kwargs["experiment_spec"],
        ),
        output_path=output_path,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=max(1, int(args.concurrency)),
        resume=bool(args.resume),
    )
    print_benchmark_summary(rows)
    print(f"OUTPUT_JSONL={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
