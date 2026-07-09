"""Unified benchmark task loading, running, and evaluation helpers."""

from __future__ import annotations

import importlib
import importlib.metadata
from typing import Any, Callable, Dict, Optional

from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.core.task import Task

from .cybench import CyBenchAdapter, run_cybench_task
from .desktop import DesktopStarterAdapter, run_desktop_starter_task
from .gaia import GaiaAdapter, run_gaia_task
from .osworld import OSWorldBenchmarkAdapter, run_osworld_task
from .tau_bench import TauBenchAdapter, run_tau_bench_task

BenchmarkRunner = Callable[..., BenchmarkRunResult | Dict[str, Any]]


def normalize_benchmark_name(value: str) -> str:
    key = str(value).strip().lower().replace("_", "-")
    aliases = {
        "tau": "tau-bench",
        "taubench": "tau-bench",
        "tau-bench": "tau-bench",
        "gaia": "gaia",
        "cybench": "cybench",
        "desktop": "desktop-starter",
        "desktop-starter": "desktop-starter",
        "osworld-starter": "desktop-starter",
        "osworld": "osworld",
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
    if normalized == "desktop-starter":
        adapter = DesktopStarterAdapter(dataset_path=root)
        rows = adapter.load_records(split=split)
        return adapter.to_tasks(rows, split=split, limit=limit)
    if normalized == "osworld":
        adapter = OSWorldBenchmarkAdapter(dataset_path=str(root or "references/OSWorld/evaluation_examples"))
        rows = adapter.load_records(split=split, domain=subset, limit=limit)
        return adapter.to_tasks(rows, split=split, limit=limit)
    adapter = GaiaAdapter(local_dir=str(root or "data/gaia"))
    rows = adapter.load_local_records(split=split, local_dir=str(root or "data/gaia"))
    return adapter.to_tasks(rows, split=split, limit=limit)


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


def resolve_builtin_runner(
    *, benchmark: str, strategy: str = "dry_run"
) -> Optional[BenchmarkRunner]:
    normalized = normalize_benchmark_name(benchmark)
    lane = str(strategy or "").strip().lower().replace("-", "_")
    if normalized == "desktop-starter" and lane in {
        "desktop_baseline",
        "desktop_starter",
        "desktop_smoke",
        "baseline",
        "smoke",
    }:
        def runner(*, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec):
            enriched = RunSpec.from_value(run_spec)
            if not enriched.prompt_protocol or enriched.prompt_protocol == "react_text_v1":
                enriched.prompt_protocol = "desktop_actions_json_v1"
            if not enriched.parser_name or enriched.parser_name == "ReActTextParser":
                enriched.parser_name = "JsonDecisionParser"
            if lane == "desktop_smoke" or lane == "smoke":
                enriched.metadata = dict(enriched.metadata or {})
                enriched.metadata["desktop_smoke"] = True
            return run_desktop_starter_task(
                task=task,
                run_spec=enriched,
                experiment_spec=experiment_spec,
            )

        return runner
    if normalized == "gaia" and lane in {
        "gaia_baseline",
        "gaia_smoke",
        "baseline",
        "smoke",
    }:
        def runner(*, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec):
            enriched = RunSpec.from_value(run_spec)
            if not enriched.prompt_protocol:
                enriched.prompt_protocol = "react_text_v1"
            if not enriched.parser_name:
                enriched.parser_name = "ReActTextParser"
            if lane in {"gaia_smoke", "smoke"}:
                enriched.metadata = dict(enriched.metadata or {})
                enriched.metadata["gaia_smoke"] = True
            return run_gaia_task(
                task=task,
                run_spec=enriched,
                experiment_spec=experiment_spec,
            )

        return runner
    if normalized == "tau-bench" and lane in {
        "tau_baseline",
        "tau_smoke",
        "baseline",
        "smoke",
    }:
        def runner(*, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec):
            enriched = RunSpec.from_value(run_spec)
            if not enriched.prompt_protocol:
                enriched.prompt_protocol = "react_text_v1"
            if not enriched.parser_name:
                enriched.parser_name = "ReActTextParser"
            if lane in {"tau_smoke", "smoke"}:
                enriched.metadata = dict(enriched.metadata or {})
                enriched.metadata["tau_smoke"] = True
            return run_tau_bench_task(
                task=task,
                run_spec=enriched,
                experiment_spec=experiment_spec,
            )

        return runner
    if normalized == "cybench" and lane in {
        "cybench_baseline",
        "cybench_smoke",
        "baseline",
        "smoke",
    }:
        def runner(*, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec):
            enriched = RunSpec.from_value(run_spec)
            if not enriched.prompt_protocol:
                enriched.prompt_protocol = "react_text_v1"
            if not enriched.parser_name:
                enriched.parser_name = "ReActTextParser"
            if lane in {"cybench_smoke", "smoke"}:
                enriched.metadata = dict(enriched.metadata or {})
                enriched.metadata["cybench_smoke"] = True
            return run_cybench_task(
                task=task,
                run_spec=enriched,
                experiment_spec=experiment_spec,
            )

        return runner
    if normalized == "osworld" and lane in {
        "osworld_baseline",
        "osworld_smoke",
        "baseline",
        "smoke",
    }:
        def runner(*, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec):
            enriched = RunSpec.from_value(run_spec)
            if not enriched.prompt_protocol or enriched.prompt_protocol == "react_text_v1":
                enriched.prompt_protocol = "desktop_actions_json_v1"
            if not enriched.parser_name or enriched.parser_name == "ReActTextParser":
                enriched.parser_name = "JsonDecisionParser"
            if lane == "osworld_smoke" or lane == "smoke":
                enriched.metadata = dict(enriched.metadata or {})
                enriched.metadata["osworld_smoke"] = True
            return run_osworld_task(
                task=task,
                run_spec=enriched,
                experiment_spec=experiment_spec,
            )

        return runner
    # Fallback: discover runners via entry_points (e.g. cybergym-agent registers itself)
    try:
        for ep in importlib.metadata.entry_points(group="qitos.benchmark.runners"):
            if ep.name == normalized:
                return ep.load()(strategy=strategy)
    except Exception:
        pass
    return None


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
