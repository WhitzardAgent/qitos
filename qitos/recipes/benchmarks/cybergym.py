"""Canonical CyberGym recipe for QitOS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from qitos.benchmark.cybergym import (
    make_trace_writer,
    prepare_task_dir,
    run_cybergym_agent_task,
    task_slug,
)


def run_cybergym_recipe_task(
    *,
    task_id: str,
    data_dir: str,
    out_dir: str,
    server: str,
    difficulty: str,
    model_name: str,
    api_key: str,
    base_url: str,
    max_steps: int | None,
    max_runtime_seconds: float,
    trace_logdir: str,
    trace_prefix: str = "qitos_cybergym",
) -> dict[str, Any]:
    out_root = Path(out_dir).expanduser().resolve()
    workspace_root = out_root / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    task_dir = prepare_task_dir(
        task_id=task_id,
        out_dir=workspace_root / task_slug(task_id),
        data_dir=data_dir,
        server=server,
        difficulty=difficulty,
    )
    return run_cybergym_agent_task(
        task_dir=task_dir,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        server=server,
        max_steps=max_steps,
        max_runtime_seconds=max_runtime_seconds,
        trace_logdir=trace_logdir,
        trace_prefix=trace_prefix,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run CyberGym through QitOS with native trace support"
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument(
        "--difficulty", default="level1", choices=["level0", "level1", "level2", "level3"]
    )
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=float, default=3600.0)
    parser.add_argument("--trace-logdir", default="runs/cybergym/traces")
    parser.add_argument("--trace-prefix", default="qitos_cybergym")
    args = parser.parse_args(argv)

    result = run_cybergym_recipe_task(
        task_id=args.task_id,
        data_dir=args.data_dir,
        out_dir=str(Path(args.out_dir)),
        server=args.server,
        difficulty=args.difficulty,
        model_name=args.model_name,
        api_key=args.api_key,
        base_url=args.base_url,
        max_steps=args.max_steps,
        max_runtime_seconds=float(args.max_runtime_seconds),
        trace_logdir=args.trace_logdir,
        trace_prefix=args.trace_prefix,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "make_trace_writer",
    "prepare_task_dir",
    "run_cybergym_agent_task",
    "run_cybergym_recipe_task",
    "task_slug",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
