import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qitos.benchmark import normalize_benchmark_name, resolve_builtin_runner
from qitos.benchmark.cybergym import CyberGymBenchmarkAdapter, make_trace_writer, task_slug
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
        with mock.patch.object(cybergym, "prepare_task_dir", return_value=Path("/tmp/task")):
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


if __name__ == "__main__":
    unittest.main()
