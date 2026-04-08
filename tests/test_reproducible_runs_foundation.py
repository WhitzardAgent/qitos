from __future__ import annotations

import json
from pathlib import Path

from qitos import Action, AgentModule, Decision, ExperimentSpec, RunSpec, StateSchema
from qitos.cli import main as qit_main
from qitos.core.spec import BenchmarkRunResult


class _State(StateSchema):
    pass


class _FinalAgent(AgentModule[_State, dict, Action]):
    def init_state(self, task: str, **kwargs: object) -> _State:
        return _State(task=task, max_steps=int(kwargs.get("max_steps", 2)))

    def decide(self, state: _State, observation: dict) -> Decision[Action]:
        _ = observation
        return Decision.final(f"done:{state.task}")

    def reduce(
        self, state: _State, observation: dict, decision: Decision[Action]
    ) -> _State:
        _ = observation
        state.final_result = str(decision.final_answer or "")
        return state


def _make_run_dir(root: Path, run_id: str) -> Path:
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "step_count": 1,
                "event_count": 1,
                "summary": {
                    "stop_reason": "final",
                    "final_result": "ok",
                    "steps": 1,
                    "failure_report": {},
                },
                "schema_version": "v1",
                "model_id": "demo-model",
                "prompt_hash": "hash",
                "tool_versions": {},
                "seed": None,
                "run_config_hash": "cfg",
                "git_sha": "sha123",
                "package_version": "0.3.0",
                "benchmark_name": "tau-bench",
                "benchmark_split": "test",
                "model_family": "Qwen",
                "prompt_protocol": "react_text_v1",
                "parser_name": "ReActTextParser",
                "tool_manifest": [],
                "run_spec": {},
                "experiment_spec": None,
                "official_run": False,
                "replay_mode": "best_effort",
                "replay_note": "QitOS records config, seed, git SHA, prompt/parser metadata, and trace artifacts for research-grade replay, but remote models and external systems may remain non-deterministic.",
                "token_usage": 0,
                "latency_seconds": 0.0,
                "cost": 0.0,
            }
        ),
        encoding="utf-8",
    )
    (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "steps.jsonl").write_text("", encoding="utf-8")
    return run


def test_run_spec_trace_manifest_integration(tmp_path: Path):
    agent = _FinalAgent()
    task_id = "tau_case_001"
    run_spec = RunSpec(
        model_family="Qwen",
        model_name="Qwen/Qwen3-8B",
        prompt_protocol="react_text_v1",
        parser_name="ReActTextParser",
        toolset_name="ToolRegistry",
        tool_manifest=[],
        environment={"type": "host"},
        stop_criteria=["FinalResultCriteria"],
        trace_schema_version="v1",
        benchmark_name="tau-bench",
        benchmark_split="test",
    )
    experiment_spec = ExperimentSpec(
        name="tau-bench:test",
        benchmark_name="tau-bench",
        benchmark_split="test",
        run_defaults={"limit": 1},
    )
    result = agent.run(
        task=f"solve {task_id}",
        trace_logdir=str(tmp_path),
        render=False,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
    )
    assert result == f"done:solve {task_id}"
    manifests = list(tmp_path.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["run_spec"]["model_name"] == "Qwen/Qwen3-8B"
    assert manifest["experiment_spec"]["name"] == "tau-bench:test"
    assert manifest["prompt_protocol"] == "react_text_v1"
    assert manifest["parser_name"] == "ReActTextParser"
    assert "git_sha" in manifest
    assert "package_version" in manifest
    assert manifest["replay_mode"] == "best_effort"
    assert "research-grade replay" in manifest["replay_note"]


def test_benchmark_run_result_roundtrip():
    row = BenchmarkRunResult(
        task_id="t1",
        benchmark="tau-bench",
        split="test",
        prediction="x",
        success=False,
        stop_reason="not_executed",
        steps=0,
        latency_seconds=0.0,
        token_usage=0,
        cost=0.0,
        trace_run_dir=None,
        run_spec_ref="abc",
    )
    loaded = BenchmarkRunResult.from_value(row.to_dict())
    assert loaded.task_id == "t1"
    assert loaded.run_spec_ref == "abc"


def test_qit_bench_run_and_eval(tmp_path: Path, capsys):
    out = tmp_path / "tau_results.jsonl"
    rc = qit_main(
        [
            "bench",
            "run",
            "--benchmark",
            "tau-bench",
            "--split",
            "test",
            "--subset",
            "retail",
            "--limit",
            "2",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["benchmark"] == "tau-bench"
    assert rows[0]["run_spec_ref"]

    capsys.readouterr()
    rc = qit_main(["bench", "eval", "--input", str(out), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 2
    assert "success_rate" in payload


def test_qit_bench_replay_and_export(tmp_path: Path, capsys):
    run = _make_run_dir(tmp_path, "bench_run")
    out = tmp_path / "bench_run.html"
    rc = qit_main(["bench", "replay", "--run", str(run), "--print-url"])
    assert rc == 0
    assert "/replay/bench_run" in capsys.readouterr().out

    rc = qit_main(["bench", "export", "--run", str(run), "--html", str(out)])
    assert rc == 0
    assert out.exists()
