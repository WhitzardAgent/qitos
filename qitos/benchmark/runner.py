"""Unified benchmark task loading, running, and evaluation helpers."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.core.task import Task

from .cybench import CyBenchAdapter
from .gaia import GaiaAdapter
from .tau_bench import TauBenchAdapter

BenchmarkRunner = Callable[..., BenchmarkRunResult | Dict[str, Any]]


def normalize_benchmark_name(value: str) -> str:
    key = str(value).strip().lower().replace("_", "-")
    aliases = {
        "tau": "tau-bench",
        "taubench": "tau-bench",
        "tau-bench": "tau-bench",
        "gaia": "gaia",
        "cybench": "cybench",
    }
    if key not in aliases:
        raise ValueError(f"Unsupported benchmark: {value}")
    return aliases[key]


def load_benchmark_tasks(
    *,
    benchmark: str,
    split: str,
    limit: Optional[int] = None,
    subset: Optional[str] = None,
    root: Optional[str] = None,
) -> list[Task]:
    normalized = normalize_benchmark_name(benchmark)
    if normalized == "tau-bench":
        adapter = TauBenchAdapter(env_name=str(subset or "retail"), task_split=split)
        rows = adapter.load_records(env_name=str(subset or "retail"), split=split)
        return adapter.to_tasks(rows, split=split, limit=limit)
    if normalized == "cybench":
        guided = split != "unguided"
        adapter = CyBenchAdapter(
            cybench_root=str(root or "references/cybench"),
            run_with_subtasks=guided,
        )
        rows = adapter.load_records(
            cybench_root=str(root or "references/cybench"),
            run_with_subtasks=guided,
            limit=limit,
        )
        return adapter.to_tasks(rows, split=split, limit=limit)
    adapter = GaiaAdapter(local_dir=str(root or "data/gaia"))
    rows = adapter.load_local_records(split=split, local_dir=str(root or "data/gaia"))
    return adapter.to_tasks(rows, split=split, limit=limit)


def build_experiment_spec(
    *,
    benchmark: str,
    split: str,
    run_spec: RunSpec,
    subset: Optional[str] = None,
    limit: Optional[int] = None,
    judge_config: Optional[Dict[str, Any]] = None,
) -> ExperimentSpec:
    name = f"{benchmark}:{split}"
    if subset:
        name = f"{name}:{subset}"
    return ExperimentSpec(
        name=name,
        benchmark_name=benchmark,
        benchmark_split=split,
        judge_config=dict(judge_config or {}),
        benchmark_metadata={
            "subset": subset,
            "limit": limit,
        },
        run_defaults={
            "run_spec": run_spec.to_dict(),
        },
    )


def write_benchmark_results(
    path: str | Path, rows: Iterable[BenchmarkRunResult]
) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.to_dict(), ensure_ascii=False))
            f.write("\n")
    return target


def read_benchmark_results(path: str | Path) -> list[BenchmarkRunResult]:
    rows: list[BenchmarkRunResult] = []
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return rows
    for line in target.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        rows.append(BenchmarkRunResult.from_value(json.loads(text)))
    return rows


def evaluate_benchmark_results(rows: Iterable[BenchmarkRunResult]) -> Dict[str, Any]:
    items = list(rows)
    total = len(items)
    success_count = sum(1 for item in items if item.success)
    avg_steps = (
        sum(int(item.steps or 0) for item in items) / total if total else 0.0
    )
    avg_latency = (
        sum(float(item.latency_seconds or 0.0) for item in items) / total
        if total
        else 0.0
    )
    avg_tokens = (
        sum(int(item.token_usage or 0) for item in items) / total if total else 0.0
    )
    total_cost = sum(float(item.cost or 0.0) for item in items)
    stop_reasons: Dict[str, int] = {}
    for item in items:
        key = str(item.stop_reason or "unknown")
        stop_reasons[key] = stop_reasons.get(key, 0) + 1
    benchmark = items[0].benchmark if items else None
    split = items[0].split if items else None
    return {
        "benchmark": benchmark,
        "split": split,
        "total": total,
        "success_count": success_count,
        "success_rate": (float(success_count) / float(total)) if total else 0.0,
        "avg_steps": avg_steps,
        "avg_latency_seconds": avg_latency,
        "avg_token_usage": avg_tokens,
        "total_cost": total_cost,
        "stop_reason_distribution": stop_reasons,
    }


def resolve_runner(path: Optional[str]) -> Optional[BenchmarkRunner]:
    if not path:
        return None
    if ":" not in path:
        raise ValueError("Runner path must look like `module.path:callable_name`.")
    module_name, attr_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    runner = getattr(module, attr_name)
    if not callable(runner):
        raise TypeError(f"Runner is not callable: {path}")
    return runner


def run_benchmark_tasks(
    *,
    tasks: list[Task],
    benchmark: str,
    split: str,
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
    runner: Optional[BenchmarkRunner] = None,
    strategy: str = "dry_run",
) -> list[BenchmarkRunResult]:
    results: list[BenchmarkRunResult] = []
    spec_ref = run_spec.fingerprint()
    for task in tasks:
        if runner is not None:
            produced = runner(
                task=task,
                run_spec=run_spec,
                experiment_spec=experiment_spec,
            )
            result = BenchmarkRunResult.from_value(produced)
        else:
            prediction: Any = None
            stop_reason = "not_executed"
            success = False
            if strategy == "objective_echo":
                prediction = task.objective
                stop_reason = "echo_objective"
            result = BenchmarkRunResult(
                task_id=str(task.id),
                benchmark=benchmark,
                split=split,
                prediction=prediction,
                success=success,
                stop_reason=stop_reason,
                steps=0,
                latency_seconds=0.0,
                token_usage=0,
                cost=0.0,
                trace_run_dir=None,
                run_spec_ref=spec_ref,
                metadata={
                    "objective": task.objective,
                    "task_metadata": dict(task.metadata or {}),
                    "strategy": strategy,
                },
            )
        results.append(result)
    return results
