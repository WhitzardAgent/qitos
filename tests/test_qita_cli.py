from __future__ import annotations

import json
from pathlib import Path

from qitos.qita.cli import (
    _build_run_diff,
    _build_handler,
    _cmd_export,
    _discover_runs,
    _render_board_html,
    _render_diff_html,
    _render_replay_html,
    _render_run_html,
    main,
)


def _make_run(root: Path, run_id: str) -> Path:
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "step_count": 1,
                "event_count": 2,
                "summary": {
                    "stop_reason": "final",
                    "final_result": "ok",
                    "steps": 1,
                    "failure_report": {},
                    "context": {
                        "tokens_total": 144,
                        "peak_occupancy_ratio": 0.74,
                        "compact_counts": {"warning": 1, "microcompact_applied": 1},
                    },
                    "parser": {
                        "error_count": 1,
                        "warning_count": 1,
                        "salvage_count": 1,
                        "error_codes": {"missing_required_field": 1},
                    },
                },
                "schema_version": "v1",
                "model_id": "x",
                "prompt_hash": "y",
                "tool_versions": {},
                "seed": None,
                "run_config_hash": "z",
                "git_sha": "abc123def456",
                "package_version": "0.3.0",
                "benchmark_name": "tau-bench",
                "benchmark_split": "test",
                "model_family": "Qwen",
                "prompt_protocol": "react_text_v1",
                "parser_name": "ReActTextParser",
                "tool_manifest": [{"name": "visit_url"}],
                "run_spec": {
                    "model_family": "Qwen",
                    "model_name": "x",
                    "prompt_protocol": "react_text_v1",
                    "parser_name": "ReActTextParser",
                    "toolset_name": "ToolRegistry",
                    "tool_manifest": [{"name": "visit_url"}],
                    "environment": {"type": "host"},
                    "seed": None,
                    "stop_criteria": ["FinalResultCriteria"],
                    "git_sha": "abc123def456",
                    "package_version": "0.3.0",
                    "trace_schema_version": "v1",
                    "benchmark_name": "tau-bench",
                    "benchmark_split": "test",
                    "metadata": {},
                },
                "experiment_spec": {
                    "name": "tau-bench:test",
                    "benchmark_name": "tau-bench",
                    "benchmark_split": "test",
                    "judge_config": {},
                    "benchmark_metadata": {"subset": "retail"},
                    "run_defaults": {"run_spec": {"parser_name": "ReActTextParser"}},
                    "metadata": {},
                },
                "official_run": True,
                "replay_mode": "best_effort",
                "replay_note": "QitOS records config, seed, git SHA, prompt/parser metadata, and trace artifacts for research-grade replay, but remote models and external systems may remain non-deterministic.",
                "token_usage": 144,
                "latency_seconds": 0.42,
                "cost": 0.0,
            }
        ),
        encoding="utf-8",
    )
    (run / "events.jsonl").write_text(
        '{"step_id":0,"phase":"INIT","ok":true,"ts":"x"}\n'
        '{"step_id":0,"phase":"DECIDE","ok":true,"ts":"y","payload":{"stage":"model_output","raw_output":"Thought: inspect the run","model_response":{"text":"Thought: inspect the run","usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14},"finish_reason":"stop","tool_calls":[{"id":"call_1","type":"function","function":{"name":"visit_url","arguments":"{\\"url\\":\\"https://example.com\\"}"}}],"model_name":"demo-model","provider":"demo-provider","metadata":{}},"context":{"input_tokens_total":3200,"occupancy_ratio":0.74}}}\n',
        encoding="utf-8",
    )
    (run / "steps.jsonl").write_text(
        '{"step_id":0,"observation":{},"decision":{},"model_response":{"text":"Thought: inspect the run","usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14},"finish_reason":"stop","tool_calls":[{"id":"call_1","type":"function","function":{"name":"visit_url","arguments":"{\\"url\\":\\"https://example.com\\"}"}}],"model_name":"demo-model","provider":"demo-provider","metadata":{}},"actions":[],"action_results":[],"tool_invocations":[],"critic_outputs":[],"state_diff":{},"context":{"context_window":8192,"input_tokens_total":3200,"history_tokens":1800,"output_tokens":240,"occupancy_ratio":0.74,"compact_events":[{"stage":"warning","before_tokens":3200,"after_tokens":3200,"saved_tokens":0},{"stage":"microcompact_applied","before_tokens":3200,"after_tokens":2400,"saved_tokens":800}]},"parser_diagnostics":{"parser":"TerminusJsonParser","contract":"terminus_json_v1","severity":"error","code":"missing_required_field","summary":"Missing required field: tools","extraction_mode":"extracted","repair_instruction":"Return valid JSON with analysis, plan, and either commands, tools, or task_complete=true.","raw_output_preview":"{\\"analysis\\":\\"x\\",\\"plan\\":\\"y\\"}"},"parser_contract":"terminus_json_v1","parser_salvage_applied":false}\n',
        encoding="utf-8",
    )
    return run


def test_discover_runs_and_export(tmp_path: Path):
    run = _make_run(tmp_path, "r1")
    runs = _discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["id"] == "r1"

    out = tmp_path / "report.html"
    rc = _cmd_export(run=str(run), html_path=str(out))
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "QitOS Trace" in content
    assert "r1" in content


def test_render_pages(tmp_path: Path):
    run = _make_run(tmp_path, "r2")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = {
        "run": str(run),
        "run_id": "r2",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [
            json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
        ],
        "events_by_step": {"0": event_lines},
    }
    board = _render_board_html()
    view = _render_run_html(payload, embedded=False)
    replay = _render_replay_html(payload, speed_ms=200)
    assert "qita board" in board
    assert "export raw" in view
    assert "QitOS Replay" in replay
    assert "context timeline" in view
    assert "parser timeline" in view
    assert "Parser Diagnostics" in view
    assert "Context occupancy timeline" in view
    assert "compact markers" in view
    assert "official run" in view
    assert "best_effort" in view
    assert "missing_required_field" in view
    assert "extracted" in view
    assert "finish_reason" in view
    assert "tool_calls" in view
    marker = '<script id="payload" type="application/json">'
    start = view.index(marker) + len(marker)
    end = view.index("</script>", start)
    payload_block = view[start:end]
    assert '"run_id": "r2"' in payload_block
    assert '"finish_reason": "stop"' in payload_block
    assert "&quot;" not in payload_block


def test_handler_routes(tmp_path: Path):
    _make_run(tmp_path, "r3")
    handler_cls = _build_handler(tmp_path)
    assert handler_cls is not None


def test_build_run_diff_and_render(tmp_path: Path):
    _make_run(tmp_path, "left")
    right = _make_run(tmp_path, "right")
    manifest = json.loads((right / "manifest.json").read_text(encoding="utf-8"))
    manifest["summary"]["stop_reason"] = "max_steps"
    manifest["step_count"] = 3
    manifest["event_count"] = 8
    manifest["token_usage"] = 512
    manifest["run_spec"]["parser_name"] = "JsonParser"
    (right / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    diff = _build_run_diff(
        {
            "run": str(tmp_path / "left"),
            "run_id": "left",
            "manifest": json.loads(
                (tmp_path / "left" / "manifest.json").read_text(encoding="utf-8")
            ),
            "events": [],
            "steps": [
                json.loads(
                    (tmp_path / "left" / "steps.jsonl").read_text(encoding="utf-8").strip()
                )
            ],
            "events_by_step": {},
        },
        {
            "run": str(right),
            "run_id": "right",
            "manifest": manifest,
            "events": [],
            "steps": [
                json.loads((right / "steps.jsonl").read_text(encoding="utf-8").strip())
            ],
            "events_by_step": {},
        },
    )
    assert diff["left"]["stop_reason"] == "final"
    assert diff["right"]["stop_reason"] == "max_steps"
    assert diff["left"]["official_run"] is True
    assert diff["left"]["replay_mode"] == "best_effort"
    assert any(item["field"].endswith("parser_name") for item in diff["config_diff"])

    html = _render_diff_html(diff, embedded=False)
    assert "QitOS Diff" in html
    assert "Run Config Diff" in html
    assert "official_run" in html
    assert "max_steps" in html


def test_main_export(tmp_path: Path):
    run = _make_run(tmp_path, "r4")
    out = tmp_path / "x.html"
    rc = main(["export", "--run", str(run), "--html", str(out)])
    assert rc == 0
    assert out.exists()
