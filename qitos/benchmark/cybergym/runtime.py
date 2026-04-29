"""CyberGym benchmark runtime helpers."""

from __future__ import annotations

from pathlib import Path

from qitos.core import ExperimentSpec, RunSpec, Task

from ..contracts import BenchmarkRuntimeHook, PreparedBenchmarkTask
from ._imports import ensure_cybergym_source_importable


def prepare_task_dir(
    *,
    task_id: str,
    out_dir: str | Path,
    data_dir: str | Path,
    server: str,
    difficulty: str,
) -> Path:
    ensure_cybergym_source_importable()
    from cybergym.task.gen_task import generate_task
    from cybergym.task.types import TaskConfig, TaskDifficulty

    out_path = Path(out_dir).expanduser().resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    generate_task(
        TaskConfig(
            task_id=task_id,
            out_dir=out_path,
            data_dir=Path(data_dir).expanduser().resolve(),
            server=server,
            difficulty=TaskDifficulty(difficulty),
        )
    )
    return out_path


class CyberGymRuntimeHook(BenchmarkRuntimeHook):
    """Attach CyberGym runtime metadata to a benchmark task."""

    def prepare(
        self, *, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec
    ) -> PreparedBenchmarkTask:
        _ = experiment_spec
        environment = dict(run_spec.environment or {})
        metadata = {
            "server": environment.get("server"),
            "data_dir": environment.get("data_dir"),
            "workspace": environment.get("workspace"),
            "difficulty": task.inputs.get("difficulty") or task.metadata.get("split"),
        }
        return PreparedBenchmarkTask(task=task, runtime_metadata=metadata)
