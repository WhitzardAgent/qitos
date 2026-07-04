from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qitos.benchmark.cybergym import CyberGymBenchmarkAdapter
import qitos.benchmark.cybergym.runner as cybergym_runner
from qitos.core import ExperimentSpec, RunSpec


def _base_task_and_specs(dynamic_environment_config: str | None = None):
    task = CyberGymBenchmarkAdapter().to_task({"task_id": "arvo:43641"}, split="level1", idx=0)
    environment = {
        "workspace": "/tmp/psy_exp/workspace",
        "data_dir": "/tmp/data",
        "server": "http://127.0.0.1:8669",
        "base_url": "http://model/v1",
        "api_key": "key",
        "trace_logdir": "/tmp/psy_exp/traces",
    }
    if dynamic_environment_config is not None:
        environment["dynamic_environment_config"] = dynamic_environment_config
    run_spec = RunSpec(
        model_name="GLM-5.1",
        benchmark_name="cybergym",
        benchmark_split="level1",
        environment=environment,
        metadata={},
    )
    experiment_spec = ExperimentSpec(
        name="cybergym:level1",
        benchmark_name="cybergym",
        benchmark_split="level1",
        run_defaults={},
    )
    return task, run_spec, experiment_spec


def test_run_cybergym_task_builds_dynamic_environment_before_agent_run() -> None:
    task, run_spec, experiment_spec = _base_task_and_specs()
    calls: list[str] = []
    task_dir = Path("/tmp/psy_exp/workspace/arvo_43641")

    def fake_prepare_task_dir(**kwargs):
        calls.append("prepare_task_dir")
        return task_dir

    def fake_prepare_dynamic_environment(**kwargs):
        calls.append("prepare_dynamic_environment")
        return {"image_tag": "cybergym-dynamic-environment:arvo_43641", "task_id": "arvo:43641"}

    def fake_run_cybergym_agent_task(**kwargs):
        calls.append("run_cybergym_agent_task")
        assert kwargs["docker_image"] == "cybergym-dynamic-environment:arvo_43641"
        return {
            "task_id": "arvo:43641",
            "final_result": "ok",
            "stop_reason": "done",
            "step_count": 1,
            "trace_run_dir": "/tmp/psy_exp/traces/run1",
            "task_result": {"success": True, "metrics": {}},
        }

    with mock.patch.dict(os.environ, {"CYBERGYM_USE_DOCKER_ENV": "1"}, clear=False):
        with mock.patch.object(cybergym_runner, "prepare_task_dir", side_effect=fake_prepare_task_dir):
            with mock.patch.object(cybergym_runner, "prepare_dynamic_environment", side_effect=fake_prepare_dynamic_environment):
                with mock.patch.object(cybergym_runner, "run_cybergym_agent_task", side_effect=fake_run_cybergym_agent_task):
                    with mock.patch.object(cybergym_runner, "CyberGymRuntimeHook") as hook_cls:
                        with mock.patch.object(cybergym_runner, "CyberGymEvaluator") as evaluator_cls:
                            with mock.patch.object(cybergym_runner, "CyberGymScorer") as scorer_cls:
                                hook_cls.return_value.prepare.return_value = SimpleNamespace(task=task, runtime_metadata={})
                                evaluator_cls.return_value.evaluate.return_value = {}
                                scorer_cls.return_value.score.side_effect = lambda **kwargs: kwargs["base_result"]

                                result = cybergym_runner.run_cybergym_task(
                                    task=task,
                                    run_spec=run_spec,
                                    experiment_spec=experiment_spec,
                                )

    assert calls == ["prepare_task_dir", "prepare_dynamic_environment", "run_cybergym_agent_task"]
    assert result.task_id == "arvo:43641"


def test_run_cybergym_task_passes_dynamic_environment_config_path() -> None:
    task, run_spec, experiment_spec = _base_task_and_specs(dynamic_environment_config="/server/concrete/config.json")
    captured: dict[str, object] = {}
    task_dir = Path("/tmp/psy_exp/workspace/arvo_43641")

    def fake_prepare_task_dir(**kwargs):
        return task_dir

    def fake_prepare_dynamic_environment(**kwargs):
        captured.update(kwargs)
        return {"image_tag": "cybergym-dynamic-environment:arvo_43641", "task_id": "arvo:43641"}

    def fake_run_cybergym_agent_task(**kwargs):
        return {
            "task_id": "arvo:43641",
            "final_result": "ok",
            "stop_reason": "done",
            "step_count": 1,
            "trace_run_dir": "/tmp/psy_exp/traces/run1",
            "task_result": {"success": True, "metrics": {}},
        }

    with mock.patch.dict(os.environ, {"CYBERGYM_USE_DOCKER_ENV": "1"}, clear=False):
        with mock.patch.object(cybergym_runner, "prepare_task_dir", side_effect=fake_prepare_task_dir):
            with mock.patch.object(cybergym_runner, "prepare_dynamic_environment", side_effect=fake_prepare_dynamic_environment):
                with mock.patch.object(cybergym_runner, "run_cybergym_agent_task", side_effect=fake_run_cybergym_agent_task):
                    with mock.patch.object(cybergym_runner, "CyberGymRuntimeHook") as hook_cls:
                        with mock.patch.object(cybergym_runner, "CyberGymEvaluator") as evaluator_cls:
                            with mock.patch.object(cybergym_runner, "CyberGymScorer") as scorer_cls:
                                hook_cls.return_value.prepare.return_value = SimpleNamespace(task=task, runtime_metadata={})
                                evaluator_cls.return_value.evaluate.return_value = {}
                                scorer_cls.return_value.score.side_effect = lambda **kwargs: kwargs["base_result"]

                                cybergym_runner.run_cybergym_task(
                                    task=task,
                                    run_spec=run_spec,
                                    experiment_spec=experiment_spec,
                                )

    assert captured["dataset_config_path"] == "/server/concrete/config.json"
