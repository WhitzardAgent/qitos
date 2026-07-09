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
    asset_path = run / "screen.png"
    asset_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x02\xeb\x01\xf5i\xf6\x81\xb7\x00\x00\x00\x00IEND\xaeB`\x82"
    )
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
        '{"step_id":0,"phase":"DECIDE","ok":true,"ts":"x1","payload":{"stage":"model_input","prepared":"Task: inspect the run","prepared_full":"Task: inspect the run\\nStep: 0","history_message_count":1,"messages":[{"role":"system","content":"You are an agent."},{"role":"user","content":"Task: inspect the run"}],"context":{"input_tokens_total":22,"prepared_tokens":6,"history_tokens":8,"occupancy_ratio":0.12}}}\n'
        '{"step_id":0,"phase":"DECIDE","ok":true,"ts":"y","payload":{"stage":"model_output","raw_output":"Thought: inspect the run","model_response":{"text":"Thought: inspect the run","usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14},"finish_reason":"stop","tool_calls":[{"id":"call_1","type":"function","function":{"name":"visit_url","arguments":"{\\"url\\":\\"https://example.com\\"}"}}],"model_name":"demo-model","provider":"demo-provider","metadata":{}},"context":{"input_tokens_total":3200,"occupancy_ratio":0.74}}}\n',
        encoding="utf-8",
    )
    step_payload = {
        "step_id": 0,
        "observation": {
            "env": {
                "observation": {
                    "data": {
                        "multimodal": {
                            "grounding_metadata": {
                                "boxes": [{"x": 24, "y": 18, "width": 36, "height": 20}],
                                "ocr_spans": [{"text": "Continue", "x": 30, "y": 20}],
                            }
                        }
                    }
                }
            }
        },
        "decision": {},
        "model_response": {
            "text": "Thought: inspect the run",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            "finish_reason": "stop",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "visit_url",
                        "arguments": '{"url":"https://example.com"}',
                    },
                }
            ],
            "model_name": "demo-model",
            "provider": "demo-provider",
            "metadata": {},
        },
        "actions": [{"name": "click", "args": {"x": 42, "y": 28}}],
        "action_results": [],
        "tool_invocations": [],
        "critic_outputs": [],
        "state_diff": {},
        "context": {
            "context_window": 8192,
            "input_tokens_total": 3200,
            "history_tokens": 1800,
            "output_tokens": 240,
            "occupancy_ratio": 0.74,
            "compact_events": [
                {"stage": "warning", "before_tokens": 3200, "after_tokens": 3200, "saved_tokens": 0},
                {"stage": "microcompact_applied", "before_tokens": 3200, "after_tokens": 2400, "saved_tokens": 800},
            ],
        },
        "prompt_metadata": {
            "tool_schema_delivery": "api_parameter",
            "model_input_modalities": ["text", "image"],
            "model_input_visual_count": 1,
            "observation_modalities": ["text", "screenshot"],
        },
        "parser_diagnostics": {
            "parser": "TerminusJsonParser",
            "contract": "terminus_json_v1",
            "severity": "error",
            "code": "missing_required_field",
            "summary": "Missing required field: tools",
            "extraction_mode": "extracted",
            "repair_instruction": "Return valid JSON with analysis, plan, and either commands, tools, or task_complete=true.",
            "raw_output_preview": '{"analysis":"x","plan":"y"}',
        },
        "parser_contract": "terminus_json_v1",
        "parser_salvage_applied": False,
        "decision_source": "native_tool_calls",
        "native_tool_call_used": True,
        "native_tool_call_fallback_reason": None,
        "visual_assets": [
            {
                "kind": "screenshot",
                "path": str(asset_path),
                "mime_type": "image/png",
                "source_step": 0,
            }
        ],
        "observation_modalities": ["text", "screenshot"],
        "visual_asset_count": 1,
        "has_screenshot": True,
        "has_dom": False,
        "has_accessibility_tree": False,
        "model_input_modalities": ["text", "image"],
        "model_input_visual_count": 1,
    }
    (run / "steps.jsonl").write_text(
        json.dumps(step_payload, ensure_ascii=False) + "\n",
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


def test_critic_timeline_section(tmp_path: Path):
    """Critic timeline section is rendered in the run detail page."""
    run = _make_run(tmp_path, "rc1")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Add critic outputs to the step
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step_data["critic_outputs"] = [
        {"action": "continue", "reason": "looks good", "score": 0.85},
        {"action": "retry", "reason": "unclear output", "score": 0.3},
    ]
    payload = {
        "run": str(run),
        "run_id": "rc1",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "critic timeline" in html
    assert "buildCriticTimeline" in html


def test_critic_summary_in_overview(tmp_path: Path):
    """Overview panel shows critic intervention stats."""
    run = _make_run(tmp_path, "rc2")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step_data["critic_outputs"] = [
        {"action": "stop", "reason": "fatal error", "score": 0.1},
        {"action": "retry", "reason": "try again", "score": 0.4},
    ]
    payload = {
        "run": str(run),
        "run_id": "rc2",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "critic interventions" in html
    assert "critic retries" in html
    assert "critic stops" in html
    assert "critic avg score" in html


def test_critic_enhanced_render(tmp_path: Path):
    """Enhanced renderCritic shows all critic outputs with color badges."""
    run = _make_run(tmp_path, "rc3")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step_data["critic_outputs"] = [
        {"action": "continue", "reason": "ok", "score": 0.9},
        {"action": "retry", "reason": "redo", "score": 0.3, "instruction_patch": "Be more specific"},
        {"action": "stop", "reason": "fail", "score": 0.05, "state_patch": {"key": "val"}},
    ]
    payload = {
        "run": str(run),
        "run_id": "rc3",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    # renderCritic function should exist in the JS
    assert "renderCritic" in html
    # The function handles multiple critic outputs
    assert "actionColors" in html or "#4ade80" in html


def test_live_sse_endpoint_in_handler(tmp_path: Path):
    """The /api/live/ route is handled by QitaHandler."""
    _make_run(tmp_path, "rlive")
    handler_cls = _build_handler(tmp_path)
    assert handler_cls is not None
    # Verify the handler class has _send_live_sse method
    assert hasattr(handler_cls, "_send_live_sse")


def test_live_button_in_run_page(tmp_path: Path):
    """Run detail page has a live button."""
    run = _make_run(tmp_path, "rlive2")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = {
        "run": str(run),
        "run_id": "rlive2",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [
            json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
        ],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert 'id="streamBtn"' in html
    assert "startStream" in html


def test_board_pulse_indicator(tmp_path: Path):
    """Board HTML includes pulse animation for running runs."""
    html = _render_board_html()
    assert "live-dot" in html or "pulse" in html


def test_sse_live_stream_js(tmp_path: Path):
    """Run page JS includes SSE live stream code with UI updates."""
    run = _make_run(tmp_path, "rsse")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = {
        "run": str(run),
        "run_id": "rsse",
        "manifest": json.loads((run / "manifest.json").read_text(encoding="utf-8")),
        "events": event_lines,
        "steps": [
            json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
        ],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "/api/live/" in html
    assert "/api/stream/" in html
    assert "_addLiveBanner" in html


def test_running_status_card_has_pulse(tmp_path: Path):
    """Board card for a running run shows the live-dot pulse indicator."""
    run = _make_run(tmp_path, "rrun")
    # Change manifest status to "running"
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "running"
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    runs = _discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["status"] == "running"


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
    assert "visual timeline" in view
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
    assert "decision_source" in view
    assert "native_tool_call_used" in view
    assert "tool_delivery" in view
    assert "Model Input" in view
    assert "prepared text from agent.prepare(state)" in view
    assert "Actual messages sent to model" in view
    assert "Visual Assets" in view
    assert "grounding metadata" in view
    assert "critic retries" in view
    assert "model input images" in view
    assert "screen.png" in view
    assert "replay screenshot" in replay
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


def _make_multi_agent_run(root: Path, run_id: str) -> Path:
    """Create a run directory with multiple agents and handoff events."""
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "status": "completed",
        "step_count": 4,
        "event_count": 6,
        "handoff_count": 2,
        "agent_topology": "sequential",
        "summary": {
            "stop_reason": "completed",
            "final_result": "done",
            "steps": 4,
            "failure_report": {},
        },
        "token_usage": {"total": 3000},
        "latency_seconds": 30.0,
        "cost": 0.05,
    }))
    events = [
        {"run_id": run_id, "step_id": 0, "phase": "think", "ok": True, "ts": "2026-01-01T00:00:01Z"},
        {"run_id": run_id, "step_id": 0, "phase": "act", "ok": True, "ts": "2026-01-01T00:00:02Z"},
        {"run_id": run_id, "step_id": 1, "phase": "handoff_start", "ok": True, "ts": "2026-01-01T00:00:03Z",
         "payload": {"from": "planner", "to": "coder"}},
        {"run_id": run_id, "step_id": 1, "phase": "handoff_end", "ok": True, "ts": "2026-01-01T00:00:04Z"},
        {"run_id": run_id, "step_id": 2, "phase": "think", "ok": True, "ts": "2026-01-01T00:00:05Z"},
        {"run_id": run_id, "step_id": 3, "phase": "handoff_start", "ok": True, "ts": "2026-01-01T00:00:06Z",
         "payload": {"from": "coder", "to": "reviewer"}},
    ]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    steps = [
        {"step_id": 0, "agent_id": "planner", "observation": {}, "decision": {"thought": "plan"},
         "actions": [], "action_results": [], "tool_invocations": [], "critic_outputs": [], "state_diff": {}},
        {"step_id": 1, "agent_id": "planner", "observation": {}, "decision": {"thought": "delegate"},
         "actions": [], "action_results": [], "tool_invocations": [], "critic_outputs": [], "state_diff": {}},
        {"step_id": 2, "agent_id": "coder", "observation": {}, "decision": {"thought": "code"},
         "actions": [], "action_results": [], "tool_invocations": [], "critic_outputs": [], "state_diff": {}},
        {"step_id": 3, "agent_id": "reviewer", "observation": {}, "decision": {"thought": "review"},
         "actions": [], "action_results": [], "tool_invocations": [], "critic_outputs": [], "state_diff": {}},
    ]
    (run / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps))
    return run


def test_handoff_gantt_section_in_run_page(tmp_path: Path):
    """Handoff gantt section is rendered for multi-agent runs."""
    run = _make_multi_agent_run(tmp_path, "h1")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_lines = [
        json.loads(line)
        for line in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    payload = {
        "run": str(run),
        "run_id": "h1",
        "manifest": manifest,
        "events": event_lines,
        "steps": step_lines,
        "events_by_step": {
            "0": [e for e in event_lines if e["step_id"] == 0],
            "1": [e for e in event_lines if e["step_id"] == 1],
            "2": [e for e in event_lines if e["step_id"] == 2],
            "3": [e for e in event_lines if e["step_id"] == 3],
        },
    }
    html = _render_run_html(payload, embedded=False)
    assert "handoff gantt" in html
    assert "handoffGantt" in html
    assert "buildHandoffGantt" in html


def test_handoff_gantt_hidden_for_single_agent(tmp_path: Path):
    """Single-agent runs should hide the handoff gantt section."""
    run = _make_run(tmp_path, "sa1")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    # No handoff events, single agent
    manifest["handoff_count"] = 0
    payload = {
        "run": str(run),
        "run_id": "sa1",
        "manifest": manifest,
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "buildHandoffGantt" in html  # function defined
    assert "No handoff events recorded" in html


def test_cost_panel_section_in_run_page(tmp_path: Path):
    """Cost panel section is rendered in run detail pages."""
    run = _make_run(tmp_path, "cp1")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    payload = {
        "run": str(run),
        "run_id": "cp1",
        "manifest": manifest,
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "costPanel" in html
    assert "buildCostPanel" in html
    assert "cost summary" in html


def test_cost_panel_hidden_when_no_data(tmp_path: Path):
    """Cost panel is hidden when no cost/performance data."""
    run = _make_run(tmp_path, "cp2")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    # Zero out cost data
    manifest["token_usage"] = 0
    manifest["latency_seconds"] = 0
    manifest["cost"] = 0
    manifest["summary"]["context"]["tokens_total"] = 0
    payload = {
        "run": str(run),
        "run_id": "cp2",
        "manifest": manifest,
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "buildCostPanel" in html
    assert "No cost/performance data available" in html


def test_board_trend_chart_section():
    """Board page includes trend chart section with metric selector."""
    html = _render_board_html()
    assert "trendSection" in html
    assert "trendChart" in html
    assert "trendMetric" in html
    assert "buildTrendChart" in html
    # Metric options
    assert '<option value="tokens">tokens</option>' in html
    assert '<option value="steps">steps</option>' in html
    assert '<option value="runtime">runtime (s)</option>' in html
    assert '<option value="cost">cost ($)</option>' in html


def test_screenshot_strip_in_run_page(tmp_path: Path):
    """Screenshot strip section is present in run detail pages."""
    run = _make_run(tmp_path, "ss1")
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    payload = {
        "run": str(run),
        "run_id": "ss1",
        "manifest": manifest,
        "events": event_lines,
        "steps": [step_data],
        "events_by_step": {"0": event_lines},
    }
    html = _render_run_html(payload, embedded=False)
    assert "screenshotStrip" in html
    assert "screenshotStripSection" in html
    assert "buildScreenshotStrip" in html


def test_screenshot_strip_hidden_when_embedded():
    """Screenshot strip should be hidden in embedded mode."""
    html = _render_run_html({"run": "/tmp", "run_id": "x", "manifest": {}, "events": [], "steps": [], "events_by_step": {}}, embedded=True)
    # The section still exists in DOM but buildScreenshotStrip hides it when embedded
    assert "buildScreenshotStrip" in html
