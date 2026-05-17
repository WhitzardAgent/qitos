"""Experiment Runner — structured multi-task, multi-config execution."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..cache import CacheBackend, DiskCache, InMemoryCache
from ..checkpoint import CheckpointManager
from ..config.loader import AgentConfig
from ..core.agent_module import AgentModule
from ..core.spec import BenchmarkRunResult, ExperimentSpec
from ..engine.engine import Engine, RuntimeBudget
from .sweep import SweepSpec, sweep_product


@dataclass
class ExperimentResult:
    """Aggregate result of an experiment run."""

    experiment_name: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    skipped_tasks: int
    results: List[BenchmarkRunResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_name": self.experiment_name,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "results": [
                r.to_dict() if hasattr(r, "to_dict") else asdict(r)
                for r in self.results
            ],
            "summary": self.summary,
            "elapsed_seconds": self.elapsed_seconds,
            "metadata": self.metadata,
        }


def _apply_sweep_to_config(
    base_config: AgentConfig, overrides: Dict[str, Any]
) -> AgentConfig:
    """Apply sweep parameter overrides to a base AgentConfig.

    Supports dotted paths like ``model.temperature`` and top-level keys
    like ``max_steps``.
    """
    import copy

    config = copy.deepcopy(base_config)

    for key, value in overrides.items():
        parts = key.split(".")
        if len(parts) == 1:
            if hasattr(config, key):
                setattr(config, key, value)
        elif len(parts) == 2 and parts[0] == "model":
            if hasattr(config.model, parts[1]):
                setattr(config.model, parts[1], value)
        else:
            config.metadata[key] = value

    return config


def _build_cache_backend(
    cache_config: Optional[Dict[str, Any]] = None,
) -> Optional[CacheBackend]:
    """Build a CacheBackend from experiment config dict."""
    if not cache_config:
        return None
    backend_type = cache_config.get("backend", "memory")
    if backend_type == "disk":
        cache_dir = cache_config.get("dir", "./runs/cache")
        return DiskCache(cache_dir)
    return InMemoryCache(
        max_entries=cache_config.get("max_entries", 256),
        default_ttl=cache_config.get("ttl"),
    )


def _build_checkpoint_manager(
    checkpoint_config: Optional[Dict[str, Any]] = None,
) -> Optional[CheckpointManager]:
    """Build a CheckpointManager from experiment config dict."""
    if not checkpoint_config:
        return None
    cp_dir = checkpoint_config.get("dir", "./runs/checkpoints")
    interval = checkpoint_config.get("interval", 1)
    return CheckpointManager(cp_dir, interval=interval)


class ExperimentRunner:
    """Execute experiments across tasks and parameter sweeps.

    Args:
        agent: An AgentModule instance to use for all runs.
        config: Base AgentConfig (used for sweep overrides and task dataset).
        sweep: Optional SweepSpec for parameter sweeps.
        experiment_spec: Optional ExperimentSpec metadata.
        cache_config: Optional dict for cache backend configuration.
        checkpoint_config: Optional dict for checkpoint configuration.
        concurrency: Number of parallel tasks (default 1 = sequential).
        resume: If True, skip tasks that already have results.
        output_dir: Directory to write results.
    """

    def __init__(
        self,
        agent: Optional[AgentModule] = None,
        config: Optional[AgentConfig] = None,
        sweep: Optional[SweepSpec] = None,
        experiment_spec: Optional[ExperimentSpec] = None,
        cache_config: Optional[Dict[str, Any]] = None,
        checkpoint_config: Optional[Dict[str, Any]] = None,
        concurrency: int = 1,
        resume: bool = False,
        output_dir: str = "./runs/experiments",
    ):
        self.agent = agent
        self.config = config or AgentConfig(name="default")
        self.sweep = sweep or SweepSpec()
        self.experiment_spec = experiment_spec or ExperimentSpec()
        self.cache_config = cache_config
        self.checkpoint_config = checkpoint_config
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self.output_dir = Path(output_dir)

    def run(
        self,
        tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> ExperimentResult:
        """Execute the experiment.

        Args:
            tasks: List of task dicts with at least a ``task`` key.
                   If None, uses tasks from the AgentConfig dataset.

        Returns:
            ExperimentResult with aggregated outcomes.
        """
        started_at = time.monotonic()

        # Resolve tasks
        task_list = tasks or self._config_tasks()
        if not task_list:
            return ExperimentResult(
                experiment_name=self.experiment_spec.name or "unnamed",
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                skipped_tasks=0,
                elapsed_seconds=time.monotonic() - started_at,
            )

        if self.agent is None:
            raise ValueError(
                "ExperimentRunner requires an `agent` (AgentModule) to run tasks. "
                "Pass it via the constructor."
            )

        # Expand sweep
        param_combos = sweep_product(self.sweep)

        # Load existing results if resuming
        existing_results = self._load_existing_results() if self.resume else {}

        # Build all (config_override, task) pairs
        all_runs: List[tuple] = []
        for combo in param_combos:
            for idx, task_item in enumerate(task_list):
                task_text = (
                    task_item.get("task", "")
                    if isinstance(task_item, dict)
                    else str(task_item)
                )
                task_id = (
                    task_item.get("id", f"task_{idx}")
                    if isinstance(task_item, dict)
                    else f"task_{idx}"
                )
                run_key = self._run_key(combo, task_id)
                if run_key in existing_results:
                    continue
                all_runs.append((combo, task_item, task_id, task_text))

        # Execute
        all_results: List[BenchmarkRunResult] = list(existing_results.values())
        completed = len(existing_results)
        failed = 0

        if self.concurrency == 1:
            for combo, task_item, task_id, task_text in all_runs:
                result = self._run_single(combo, task_id, task_text)
                if result is not None:
                    all_results.append(result)
                    completed += 1
                else:
                    failed += 1
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                futures = {
                    pool.submit(
                        self._run_single, combo, task_id, task_text
                    ): (combo, task_id)
                    for combo, task_item, task_id, task_text in all_runs
                }
                for future in as_completed(futures):
                    result = future.result()
                    if result is not None:
                        all_results.append(result)
                        completed += 1
                    else:
                        failed += 1

        elapsed = time.monotonic() - started_at
        summary = self._compute_summary(all_results)

        exp_result = ExperimentResult(
            experiment_name=self.experiment_spec.name or "unnamed",
            total_tasks=len(task_list) * len(param_combos),
            completed_tasks=completed,
            failed_tasks=failed,
            skipped_tasks=len(existing_results),
            results=all_results,
            summary=summary,
            elapsed_seconds=elapsed,
        )

        # Persist
        self._save_results(exp_result)
        return exp_result

    def _config_tasks(self) -> List[Dict[str, Any]]:
        """Extract tasks from AgentConfig dataset."""
        if not self.config.dataset:
            return []
        return [
            {
                "task": item.task,
                "expected": item.expected,
                "metadata": item.metadata,
            }
            if hasattr(item, "task")
            else item
            for item in self.config.dataset
        ]

    def _run_single(
        self,
        combo: Dict[str, Any],
        task_id: str,
        task_text: str,
    ) -> Optional[BenchmarkRunResult]:
        """Run a single task with a given parameter combination."""
        try:
            # Apply sweep overrides to max_steps
            max_steps = self.config.max_steps
            for key, value in combo.items():
                if key == "max_steps":
                    max_steps = value

            budget = RuntimeBudget(max_steps=max_steps)

            # Build cache/checkpoint per-run to avoid shared mutable state
            cache_backend = _build_cache_backend(self.cache_config)
            checkpoint_manager = _build_checkpoint_manager(self.checkpoint_config)

            engine = Engine(
                agent=self.agent,
                budget=budget,
                cache_backend=cache_backend,
                checkpoint_manager=checkpoint_manager,
            )

            engine_result = engine.run(task_text)

            return BenchmarkRunResult(
                task_id=task_id,
                benchmark=self.experiment_spec.benchmark_name or "",
                split=self.experiment_spec.benchmark_split or "",
                prediction=engine_result.state.final_result,
                success=engine_result.state.stop_reason == "completed",
                stop_reason=engine_result.state.stop_reason,
                steps=engine_result.step_count,
                latency_seconds=engine_result.runtime_seconds,
                token_usage=engine_result.total_tokens,
                cost=0.0,
                trace_run_dir=None,
                run_spec_ref=None,
                metadata={"sweep": combo},
            )
        except Exception as exc:
            return BenchmarkRunResult(
                task_id=task_id,
                benchmark=self.experiment_spec.benchmark_name or "",
                split=self.experiment_spec.benchmark_split or "",
                prediction=None,
                success=False,
                stop_reason="error",
                steps=0,
                latency_seconds=0.0,
                token_usage=0,
                cost=0.0,
                trace_run_dir=None,
                run_spec_ref=None,
                metadata={"sweep": combo, "error": str(exc)},
            )

    def _run_key(self, combo: Dict[str, Any], task_id: str) -> str:
        """Generate a unique key for a (combo, task) pair."""
        combo_str = json.dumps(combo, sort_keys=True)
        return f"{task_id}::{combo_str}"

    def _load_existing_results(self) -> Dict[str, BenchmarkRunResult]:
        """Load previously saved results for resume."""
        results_file = self.output_dir / "results.json"
        if not results_file.exists():
            return {}
        try:
            with open(results_file, encoding="utf-8") as f:
                data = json.load(f)
            existing: Dict[str, BenchmarkRunResult] = {}
            for row in data.get("results", []):
                task_id = row.get("task_id", "")
                sweep = row.get("metadata", {}).get("sweep", {})
                key = self._run_key(sweep, task_id)
                existing[key] = BenchmarkRunResult.from_value(row)
            return existing
        except (json.JSONDecodeError, KeyError, OSError):
            return {}

    def _compute_summary(
        self, results: List[BenchmarkRunResult]
    ) -> Dict[str, Any]:
        """Compute aggregate summary statistics."""
        if not results:
            return {}
        successes = sum(1 for r in results if r.success)
        total = len(results)
        avg_steps = sum(r.steps for r in results) / total if total else 0
        avg_latency = (
            sum(r.latency_seconds for r in results) / total if total else 0
        )
        avg_tokens = sum(r.token_usage for r in results) / total if total else 0
        total_cost = sum(r.cost for r in results)
        return {
            "success_rate": successes / total if total else 0,
            "total_runs": total,
            "successful_runs": successes,
            "avg_steps": round(avg_steps, 2),
            "avg_latency_seconds": round(avg_latency, 2),
            "avg_token_usage": round(avg_tokens, 0),
            "total_cost": round(total_cost, 4),
        }

    def _save_results(self, result: ExperimentResult) -> None:
        """Persist experiment results to disk."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "results.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)


__all__ = ["ExperimentRunner", "ExperimentResult"]
