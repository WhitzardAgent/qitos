"""Shared benchmark example helpers for the canonical v0.3 flow."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from qitos.benchmark import (
    build_experiment_spec,
    evaluate_benchmark_results,
    read_benchmark_results,
    write_benchmark_results,
)
from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec

ExampleRunner = Callable[..., BenchmarkRunResult | Dict[str, Any]]


def build_example_specs(
    *,
    benchmark: str,
    split: str,
    model_name: Optional[str],
    trace_logdir: str,
    parser_name: str = "ReActTextParser",
    toolset_name: Optional[str] = None,
    subset: Optional[str] = None,
    seed: Optional[int] = None,
    limit: Optional[int] = None,
    workspace: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> tuple[RunSpec, ExperimentSpec]:
    run_spec = RunSpec.infer(
        model_name=model_name,
        prompt_protocol="react_text_v1",
        parser_name=parser_name,
        toolset_name=toolset_name,
        benchmark_name=benchmark,
        benchmark_split=split,
        trace_schema_version="v1",
        seed=seed,
        environment={
            "trace_logdir": str(trace_logdir),
            "workspace": str(workspace or ""),
        },
        metadata=dict(metadata or {}),
    )
    experiment_spec = build_experiment_spec(
        benchmark=benchmark,
        split=split,
        run_spec=run_spec,
        subset=subset,
        limit=limit,
    )
    return run_spec, experiment_spec


def default_output_path(
    root: str | Path, *, benchmark: str, split: str, suffix: str = ".jsonl"
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(root).expanduser().resolve() / f"{benchmark}_{split}_{stamp}{suffix}"


def execute_example_jobs(
    *,
    jobs: Iterable[Dict[str, Any]],
    runner: ExampleRunner,
    output_path: str | Path,
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
    concurrency: int = 1,
    resume: bool = False,
) -> List[BenchmarkRunResult]:
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    job_items = list(jobs)
    existing_rows = read_benchmark_results(target) if resume and target.exists() else []
    done = {
        str(row.metadata.get("job_key", "")).strip() or str(row.task_id).strip()
        for row in existing_rows
    }
    pending = [
        item
        for item in job_items
        if str(item.get("job_key", "") or item.get("task_id", "")).strip() not in done
    ]

    created: List[BenchmarkRunResult] = []
    if int(concurrency) <= 1:
        for item in pending:
            created.append(_run_example_job(runner, item, run_spec, experiment_spec))
    else:
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
            futures = [
                pool.submit(
                    _run_example_job,
                    runner,
                    item,
                    run_spec,
                    experiment_spec,
                )
                for item in pending
            ]
            for future in as_completed(futures):
                created.append(future.result())

    all_rows = list(existing_rows) + created
    write_benchmark_results(target, all_rows)
    return all_rows


def print_benchmark_summary(rows: Iterable[BenchmarkRunResult]) -> None:
    summary = evaluate_benchmark_results(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def print_single_result(row: BenchmarkRunResult) -> None:
    print(json.dumps(row.to_dict(), ensure_ascii=False, indent=2))


def _run_example_job(
    runner: ExampleRunner,
    item: Dict[str, Any],
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
) -> BenchmarkRunResult:
    produced = runner(
        **item,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
    )
    row = BenchmarkRunResult.from_value(produced)
    metadata = dict(row.metadata or {})
    if item.get("job_key") is not None:
        metadata.setdefault("job_key", item.get("job_key"))
    row.metadata = metadata
    if not row.run_spec_ref:
        row.run_spec_ref = run_spec.fingerprint()
    return row
