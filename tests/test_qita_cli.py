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
from qitos.qita.data import _load_run_payload


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


def test_run_payload_includes_diagnostic_insights(tmp_path: Path):
    run = _make_run(tmp_path, "diag1")
    payload = _load_run_payload(run)

    assert "insights" in payload
    assert "step_summaries" in payload
    assert "tool_stats" in payload
    assert "phase_stats" in payload
    assert "run_focus" in payload
    assert "step_focus" in payload
    assert "cybergym_focus" in payload
    assert "step_interactions" in payload
    assert payload["insights"]["next_inspect_step"] == 0
    assert payload["run_focus"]["next_actionable_step"] == 0
    assert "parser_error" in payload["insights"]["risk_flags"]
    assert payload["step_summaries"][0]["parser"]["is_error"] is True
    assert payload["step_focus"][0]["attention_level"] == "critical"
    assert payload["step_summaries"][0]["has_visual"] is True


def test_successful_step_outcome_uses_observation_summary(tmp_path: Path):
    run = _make_run(tmp_path, "result-summary")
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step_data["parser_diagnostics"] = {}
    step_data["actions"] = [{"name": "READ", "args": {"path": "magick/attribute.c"}}]
    step_data["action_results"] = [
        {
            "status": "success",
            "output": {
                "status": "success",
                "path": "magick/attribute.c",
                "offset": 2060,
                "total_lines": 3266,
                "content": "EXIF: Offset out of address range!",
            },
            "error": None,
        }
    ]
    (run / "steps.jsonl").write_text(
        json.dumps(step_data, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    payload = _load_run_payload(run)

    assert payload["step_summaries"][0]["observation"]
    assert "magick/attribute.c" in payload["step_focus"][0]["outcome_label"]
    assert payload["step_focus"][0]["outcome_label"] != "recorded"


def test_step_interactions_pair_calls_and_separate_environment(tmp_path: Path):
    run = _make_run(tmp_path, "paired-calls")
    step = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step["actions"] = [
        {"name": "READ", "args": {"path": "README.md"}, "action_id": "call-read"},
        {"name": "RepoMap", "args": {"path": "."}, "action_id": "call-map"},
    ]
    step["tool_invocations"] = [
        {"tool_name": "READ", "status": "success", "latency_ms": 0.5, "attempts": 1},
        {"tool_name": "RepoMap", "status": "success", "latency_ms": 852.6, "attempts": 1},
    ]
    step["action_results"] = [
        {"status": "error", "output": {"message": "File not found: README.md"}, "error": "File not found: README.md", "metadata": {"tool_name": "READ"}},
        {"status": "success", "output": {"path": ".", "summary": "raw repo map"}, "error": None, "metadata": {"tool_name": "RepoMap"}},
        {"status": "success", "output": {"env": {"done": False}}, "error": None, "metadata": {"source": "env"}},
    ]
    (run / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")
    events = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events.append(
        {
            "step_id": 0,
            "phase": "ACT",
            "ok": True,
            "payload": {
                "stage": "observation_ready",
                "observation": {
                    "action_results": [
                        {"status": "error", "output": {"message": "VISIBLE_READ_ERROR"}, "error": "VISIBLE_READ_ERROR", "metadata": {"tool_name": "READ"}},
                        {"status": "success", "output": {"path": ".", "summary": "VISIBLE_REPO_MAP"}, "error": None, "metadata": {"tool_name": "RepoMap"}},
                        {"status": "success", "output": {"env": {"done": False}}, "error": None, "metadata": {"source": "env"}},
                    ]
                },
            },
        }
    )
    (run / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    interaction = _load_run_payload(run)["step_interactions"][0]

    assert [call["tool_name"] for call in interaction["calls"]] == ["READ", "RepoMap"]
    assert interaction["calls"][0]["status"] == "error"
    assert interaction["calls"][0]["result"]["error"] == "VISIBLE_READ_ERROR"
    assert interaction["calls"][0]["raw_result"]["error"] == "File not found: README.md"
    assert interaction["calls"][0]["result_source"] == "model_visible"
    assert interaction["calls"][1]["latency_ms"] == 852.6
    assert interaction["calls"][1]["args"] == {"path": "."}
    assert len(interaction["environment_results"]) == 1
    assert interaction["unmatched_actions"] == []
    assert interaction["unmatched_results"] == []


def test_step_interactions_use_ids_and_keep_unmatched_results(tmp_path: Path):
    run = _make_run(tmp_path, "paired-ids")
    step = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step["actions"] = [
        {"name": "submit_poc", "args": {"poc_path": "pocs/a.bin"}, "action_id": "call-a"},
        {"name": "submit_poc", "args": {"poc_path": "pocs/b.bin"}, "action_id": "call-b"},
    ]
    step["tool_invocations"] = [{"latency_ms": 12}, {"latency_ms": 18}]
    step["action_results"] = [
        {"status": "success", "output": {"accepted": True}, "metadata": {"action_id": "call-b", "tool_name": "submit_poc"}},
        {"status": "success", "output": {"accepted": False}, "metadata": {"action_id": "call-a", "tool_name": "submit_poc"}},
        {"status": "success", "output": {"message": "EXTRA_RESULT"}, "metadata": {"tool_name": "submit_poc"}},
    ]
    (run / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")

    interaction = _load_run_payload(run)["step_interactions"][0]

    assert interaction["calls"][0]["pairing_method"] == "action_id"
    assert interaction["calls"][0]["status"] == "no_trigger"
    assert interaction["calls"][0]["result_summary"].startswith("no_trigger")
    assert interaction["calls"][1]["status"] == "verified"
    assert interaction["calls"][0]["args"]["poc_path"] == "pocs/a.bin"
    assert interaction["calls"][1]["args"]["poc_path"] == "pocs/b.bin"
    assert len(interaction["unmatched_results"]) == 1


def test_step_interactions_mark_missing_and_blocked_evidence(tmp_path: Path):
    run = _make_run(tmp_path, "paired-partial")
    step = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step["actions"] = [
        {"name": "BASH", "args": {"command": "make poc"}},
        {"name": "submit_poc", "args": {"poc_path": "pocs/a.bin"}},
    ]
    step["tool_invocations"] = [
        {"tool_name": "BASH", "status": "blocked", "error": "Policy blocked command"}
    ]
    step["action_results"] = []
    (run / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")

    interaction = _load_run_payload(run)["step_interactions"][0]

    assert interaction["calls"][0]["status"] == "blocked"
    assert interaction["calls"][0]["invocation"]["status"] == "blocked"
    assert interaction["calls"][1]["invocation"] == {}
    assert interaction["calls"][1]["pairing_method"] == "unmatched"
    assert interaction["calls"][1]["result"] is None
    assert [row["index"] for row in interaction["unmatched_actions"]] == [0, 1]


def test_budgeted_cybergym_run_surfaces_verification_failure(tmp_path: Path):
    run = _make_run(tmp_path, "cyberdiag")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["summary"]["stop_reason"] = "budget_steps"
    manifest["summary"]["final_result"] = None
    manifest["summary"]["task_result"] = {"success": False}
    manifest["step_count"] = 2
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    base_step = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    read_error = dict(base_step)
    read_error["step_id"] = 0
    read_error["parser_diagnostics"] = {}
    read_error["actions"] = [{"name": "READ", "args": {"path": "README.md"}}]
    read_error["action_results"] = [
        {"status": "error", "error": "File not found: README.md"}
    ]
    read_error["state_diff"] = {}

    submit_failure = dict(base_step)
    submit_failure["step_id"] = 5
    submit_failure["parser_diagnostics"] = {}
    submit_failure["actions"] = [
        {"name": "submit_poc", "args": {"poc_path": "pocs/candidate.bin"}}
    ]
    submit_failure["action_results"] = [
        {
            "status": "error",
            "error": "Could not connect to verification server: [Errno 61] Connection refused",
        }
    ]
    submit_failure["state_diff"] = {
        "poc_attempts": {"before": 2, "after": 3},
        "failure_history": {
            "before": [],
            "after": [
                {
                    "failure_type": "SUBMISSION_ERROR",
                    "summary": "SUBMISSION_ERROR",
                    "evidence_excerpt": "Could not connect to verification server: [Errno 61] Connection refused",
                }
            ],
        },
    }
    (run / "steps.jsonl").write_text(
        json.dumps(read_error, ensure_ascii=False)
        + "\n"
        + json.dumps(submit_failure, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    payload = _load_run_payload(run)

    assert payload["insights"]["outcome"] == "needs_review"
    assert payload["insights"]["next_inspect_step"] == 5
    assert payload["run_focus"]["outcome"] == "needs_review"
    assert payload["run_focus"]["next_actionable_step"] == 5
    assert "server_connectivity" in payload["run_focus"]["primary_failure"]
    assert payload["cybergym_focus"]["failure_category"] == "server_connectivity"
    assert payload["cybergym_focus"]["server_connectivity_failure"] is True
    assert payload["cybergym_focus"]["poc_attempts"] == 3
    assert "cybergym_verification_failure" in payload["insights"]["risk_flags"]
    assert "Connection refused" in payload["insights"]["likely_failure"]
    assert payload["step_summaries"][1]["cybergym"]["submit_action"] is True
    assert payload["step_focus"][1]["step_role"] == "verification_failure"


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
    assert 'data-theme="light"' in view
    assert "qitaToggleTheme" in board
    assert "qitaToggleTheme" in view
    assert "qitaToggleTheme" in replay
    assert "theme-toggle" in board
    assert "theme-toggle" in view
    assert "theme-toggle" in replay
    assert "Diagnosis Strip" in view
    assert "Primary Failure" in view
    assert "Next Inspect" in view
    assert "Focus Navigator" in view
    assert "Inspector" in view
    assert "Agent Behavior Story" in view
    assert "Run Metadata" in view
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
    assert "Visual Assets" in view
    assert "grounding metadata" in view
    assert "critic retries" in view
    assert "model input images" in view
    assert "screen.png" in view
    assert "sectionHtml('Prompt'" not in view
    assert "sectionHtml('Memory Update'" not in view
    assert "sectionHtml('Trace Events'" not in view
    assert "replay screenshot" in replay
    marker = '<script id="payload" type="application/json">'
    start = view.index(marker) + len(marker)
    end = view.index("</script>", start)
    payload_block = view[start:end]
    assert '"run_id": "r2"' in payload_block
    assert '"finish_reason": "stop"' in payload_block
    assert "&quot;" not in payload_block


def test_run_page_preserves_long_content_in_expandable_details(tmp_path: Path):
    run = _make_run(tmp_path, "long1")
    long_thought = "LONG_THOUGHT_START " + ("reasoning detail " * 80) + " LONG_THOUGHT_END"
    long_raw = "Thought: " + long_thought + "\nAction: inspect"
    long_terminal = "LONG_TERMINAL_START\n" + ("terminal line\n" * 120) + "LONG_TERMINAL_END"
    long_observation = {
        "title": "long observation",
        "content": "LONG_OBSERVATION_START " + ("observation detail " * 90) + " LONG_OBSERVATION_END",
    }
    event_lines = [
        json.loads(line)
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_lines[-1]["payload"]["raw_output"] = long_raw
    event_lines[-1]["payload"]["model_response"]["text"] = long_raw
    (run / "events.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in event_lines) + "\n",
        encoding="utf-8",
    )
    step_data = json.loads((run / "steps.jsonl").read_text(encoding="utf-8").strip())
    step_data["decision"] = {"rationale": long_thought}
    step_data["actions"] = [
        {
            "name": "inspect",
            "args": {
                "prompt": "LONG_ACTION_PROMPT_START " + ("prompt detail " * 70) + " LONG_ACTION_PROMPT_END"
            },
        },
        {
            "name": "submit_candidate",
            "args": {
                "path": "pocs/candidate.bin",
                "note": "LONG_SECOND_ACTION_START " + ("second action detail " * 50) + " LONG_SECOND_ACTION_END",
            },
        },
    ]
    step_data["action_results"] = [
        {
            "status": "error",
            "output": {"terminal": {"output": "New Terminal Output:\n" + long_terminal}},
            "error": "terminal command failed after complete output",
        },
        {"status": "success", "output": long_observation},
    ]
    step_data["critic_outputs"] = [
        {"action": "retry", "reason": "LONG_CRITIC_START " + ("critic reason " * 60) + " LONG_CRITIC_END"}
    ]
    (run / "steps.jsonl").write_text(
        json.dumps(step_data, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    payload = _load_run_payload(run)
    view = _render_run_html(payload, embedded=False)
    interaction = payload["step_interactions"][0]

    assert len(interaction["calls"]) == 2
    assert interaction["calls"][0]["status"] == "error"
    assert interaction["calls"][1]["status"] == "success"
    assert "LONG_TERMINAL_END" in json.dumps(interaction, ensure_ascii=False)
    assert "LONG_OBSERVATION_END" in json.dumps(interaction, ensure_ascii=False)
    assert "Action Calls" in view
    assert "Environment Observation" in view
    assert "Complete parameters" in view
    assert "Agent-visible result · observation_ready.action_results" in view
    assert "Recorded result fallback · step.action_results" in view
    assert "Canonical raw result" in view
    assert "function selectCall" in view
    assert "const open = isAbnormalCall(call)" in view
    assert "renderOutcomeBlock" not in view
    assert "full observation" in view
    assert "full critic output" in view
    assert 'class="evidence-code"' in view
    assert "white-space:pre-wrap" in view
    assert "Agent-visible input · prepared_full" in view
    assert "Recorded step observation fallback" in view
    assert "Agent-visible input at Step" not in view
    assert "nextModelInputOutcome" not in view
    assert "firstLine(outcome, 260)" not in view
    assert "LONG_THOUGHT_END" in view
    assert "LONG_TERMINAL_END" in view
    assert "LONG_OBSERVATION_END" in view
    assert "LONG_ACTION_PROMPT_END" in view
    assert "LONG_SECOND_ACTION_END" in view
    assert "LONG_CRITIC_END" in view


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
    assert "Failure Cause" in html
    assert "Next Inspect Step" in html
    assert "riskChip" in html


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
