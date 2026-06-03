"""Benchmark-generic task/result helpers that do not depend on family runners."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.tracing.config import _redact_dict


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
            f.write(json.dumps(_redact_dict(row.to_dict()), ensure_ascii=False))
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
    failure_tags: Dict[str, int] = {}
    for item in items:
        key = str(item.stop_reason or "unknown")
        stop_reasons[key] = stop_reasons.get(key, 0) + 1
        meta = dict(item.metadata or {})
        for tag in list(meta.get("failure_tags") or []):
            token = str(tag or "").strip()
            if not token:
                continue
            failure_tags[token] = failure_tags.get(token, 0) + 1
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
        "failure_tag_distribution": failure_tags,
    }


__all__ = [
    "build_experiment_spec",
    "evaluate_benchmark_results",
    "read_benchmark_results",
    "write_benchmark_results",
]
