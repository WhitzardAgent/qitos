import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import types

from qitos.benchmark import normalize_benchmark_name, resolve_builtin_runner
from qitos.core import ExperimentSpec, RunSpec
from qitos.benchmark.cybergym import CyberGymBenchmarkAdapter, make_trace_writer, task_slug
from qitos.benchmark.cybergym._imports import (
    ensure_cybergym_source_importable,
    resolve_cybergym_source_root,
)
import qitos.benchmark.cybergym.runner as cybergym_runner
from qitos.recipes.benchmarks import cybergym


class CybergymRecipeTests(unittest.TestCase):
    def test_task_slug_replaces_colon(self):
        self.assertEqual(task_slug("arvo:1065"), "arvo_1065")
        self.assertEqual(task_slug("oss-fuzz:42535201"), "oss-fuzz_42535201")

    def test_make_trace_writer_uses_prefix_and_task_slug(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = make_trace_writer(
                trace_logdir=tmpdir,
                trace_prefix="qitos_cybergym",
                task_id="arvo:1065",
                model_id="GLM-5.1-sii",
            )

            self.assertTrue(writer.run_id.startswith("qitos_cybergym_arvo_1065_"))
            self.assertEqual(writer.metadata["model_id"], "GLM-5.1-sii")
            self.assertTrue(Path(writer.run_dir).exists())

    def test_adapter_builds_qitos_task_from_task_id(self):
        adapter = CyberGymBenchmarkAdapter()

        task = adapter.to_task({"task_id": "arvo:1065"}, split="level1", idx=0)

        self.assertEqual(task.id, "arvo:1065")
        self.assertEqual(task.inputs["difficulty"], "level1")
        self.assertEqual(task.metadata["benchmark"], "cybergym")

    def test_cybergym_is_registered_as_benchmark_family(self):
        self.assertEqual(normalize_benchmark_name("cybergym"), "cybergym")
        self.assertIsNotNone(resolve_builtin_runner(benchmark="cybergym", strategy="smoke"))

    def test_recipe_reuses_benchmark_family_helpers(self):
        self.assertIs(cybergym.task_slug, task_slug)
        self.assertIs(cybergym.make_trace_writer, make_trace_writer)

    def test_recipe_passes_runtime_budget_without_step_cap(self):
        with mock.patch.object(cybergym, "prepare_task_dir", return_value=Path("/tmp/out/workspace/arvo_1065")):
            with mock.patch.object(cybergym, "run_cybergym_agent_task", return_value={}) as run:
                cybergym.run_cybergym_recipe_task(
                    task_id="arvo:1065",
                    data_dir="data",
                    out_dir="out",
                    server="http://server",
                    difficulty="level1",
                    model_name="GLM-5.1-sii",
                    api_key="key",
                    base_url="http://model/v1",
                    max_steps=None,
                    max_runtime_seconds=3600,
                    trace_logdir="runs/cybergym/traces",
                )

        kwargs = run.call_args.kwargs
        self.assertIsNone(kwargs["max_steps"])
        self.assertEqual(kwargs["max_runtime_seconds"], 3600)
        self.assertEqual(str(kwargs["task_dir"]), "/tmp/out/workspace/arvo_1065")

    def test_runner_uses_task_root_workspace_and_keeps_source_root_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_root = Path(tmpdir).resolve()
            source_root = task_root / "repo-vul" / "project"
            source_root.mkdir(parents=True)

            fake_task = SimpleNamespace(
                id="arvo:1065",
                inputs={
                    "task_id": "arvo:1065",
                    "agent_id": "agent",
                    "checksum": "checksum",
                    "server_url": "http://server",
                    "source_root": str(source_root),
                    "repo_dir": str(task_root / "repo-vul"),
                    "task_root": str(task_root),
                    "description": "desc",
                    "error_txt": "",
                    "patch_diff": "",
                },
            )
            fake_agent = mock.Mock()
            fake_agent.run.return_value = SimpleNamespace(
                state=SimpleNamespace(stop_reason="final", final_result="ok"),
                step_count=1,
                task_result=None,
            )

            with mock.patch(
                "qitos.benchmark.cybergym.agent.adapter.CyberGymAdapter"
            ) as adapter_cls, mock.patch(
                "qitos.benchmark.cybergym.agent.cli.build_agent",
                return_value=fake_agent,
            ) as build_agent, mock.patch(
                "qitos.benchmark.cybergym.agent.stop_criteria.PoCVerificationCriteria",
                return_value=object(),
            ), mock.patch.object(cybergym_runner, "HostEnv") as host_env:
                adapter_cls.return_value.from_task_dir.return_value = fake_task
                cybergym_runner.run_cybergym_agent_task(
                    task_dir=str(task_root),
                    model_name="GLM-5.1",
                    api_key="key",
                    base_url="http://model/v1",
                    server="http://server",
                    max_steps=None,
                    max_runtime_seconds=3600,
                    trace_logdir=str(task_root / "traces"),
                )

            build_kwargs = build_agent.call_args.kwargs
            self.assertEqual(build_kwargs["workspace_root"], str(task_root))
            self.assertEqual(build_kwargs["task_root"], str(task_root))
            host_env.assert_called_once_with(workspace_root=str(task_root))
            run_kwargs = fake_agent.run.call_args.kwargs
            self.assertGreaterEqual(run_kwargs["context_config"].tool_result_max_chars, 50000)
            self.assertEqual(run_kwargs["workspace"], str(task_root))
            self.assertEqual(run_kwargs["source_root"], str(source_root))
            self.assertEqual(run_kwargs["repo_dir"], str(source_root))

    def test_resolve_cybergym_source_root_prefers_workspace_sibling(self):
        root = resolve_cybergym_source_root()

        self.assertEqual(
            root,
            Path("/data/pxd-team/workspace-149/zwq/cybergym").resolve(),
        )

    def test_ensure_cybergym_source_importable_prepends_src_and_evicts_stale_modules(self):
        stale = types.ModuleType("cybergym")
        stale.__file__ = "/home/pgroup/data3t/pgroup/zwq/cybergym/src/cybergym/__init__.py"
        stale_sub = types.ModuleType("cybergym.task")
        stale_sub.__file__ = "/home/pgroup/data3t/pgroup/zwq/cybergym/src/cybergym/task/__init__.py"
        original_path = list(sys.path)
        stale_path = "/home/pgroup/data3t/pgroup/zwq/cybergym/src"

        with mock.patch.dict(
            sys.modules,
            {"cybergym": stale, "cybergym.task": stale_sub},
            clear=False,
        ):
            with mock.patch.object(sys, "path", [stale_path, *original_path]):
                root = ensure_cybergym_source_importable()
                expected_src = str((root / "src").resolve())

                self.assertEqual(root, Path("/data/pxd-team/workspace-149/zwq/cybergym").resolve())
                self.assertEqual(sys.path[0], expected_src)
                self.assertNotIn(stale_path, sys.path)
                self.assertNotIn("cybergym", sys.modules)
                self.assertNotIn("cybergym.task", sys.modules)


if __name__ == "__main__":
    unittest.main()
