from __future__ import annotations

from types import SimpleNamespace

from qitos.core.action import Action
from qitos.core.tool_result import ToolResult
from qitos.engine._model_runtime import _ModelRuntime
from rich.console import Console

from qitos.render.content_renderer import ContentFirstRenderer
from qitos.render.events import RenderEvent
from qitos.render.hooks import ClaudeStyleHook, RenderStreamHook


def test_content_first_renderer_core_blocks() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)

    thought_evt = RenderEvent(
        channel="thinking",
        node="decision",
        step_id=0,
        payload={"rationale": "First inspect the target page and extract key points."},
    )
    thought = renderer.thought_text(thought_evt)
    assert isinstance(thought, str)
    assert "inspect the target page" in thought

    action_evt = RenderEvent(
        channel="action",
        node="planned_actions",
        step_id=0,
        payload={
            "actions": [
                {"name": "web_search", "args": {"query": "finding nemo fish species"}}
            ]
        },
    )
    action = renderer.action_summary(action_evt)
    assert isinstance(action, dict)
    assert action.get("label") == "WEB SEARCH"
    assert "finding nemo fish species" in str(action.get("detail"))

    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                {
                    "results": [
                        {
                            "title": "All fishes in Finding Nemo",
                            "url": "https://example.com/nemo/fishes",
                        },
                    ]
                }
            ]
        },
    )
    obs = renderer.observation_summary(obs_evt)
    assert isinstance(obs, dict)
    assert obs.get("title") == "Search Results"


def test_action_object_is_rendered_without_raw_repr() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    evt = RenderEvent(
        channel="action",
        node="planned_actions",
        step_id=0,
        payload={
            "actions": [
                Action(
                    name="READ",
                    args={
                        "path": "/very/long/workspace/repo-vul/src/parser/configuration/file.c",
                        "offset": 120,
                        "limit": 40,
                    },
                    action_id="call_1",
                )
            ]
        },
    )

    action = renderer.action_summary(evt)

    assert isinstance(action, dict)
    assert action["label"] == "READ"
    assert "Action(" not in action["detail"]
    assert "offset=120" in action["detail"]
    assert "file.c" in action["detail"]


def test_tool_result_observations_use_tool_specific_summaries() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=240)
    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                ToolResult(
                    status="success",
                    output={
                        "summary": "[SUBMIT(poc_path=poc.bin)]\n  ! VUL TRIGGERED -- the PoC crashed the vulnerable target",
                        "poc_path": "poc.bin",
                        "vul_exit_code": 1,
                        "verification_status": "vul_only_triggered",
                        "oracle_outcome": "VUL_CRASH",
                    },
                    metadata={"tool_name": "SUBMIT"},
                ).to_dict()
            ]
        },
    )

    obs = renderer.observation_summary(obs_evt)

    assert isinstance(obs, dict)
    assert obs["title"] == "SUBMIT Result"
    assert "VUL TRIGGERED" in obs["body"]
    assert "vul_only_triggered" in obs["body"]


def test_recall_observation_has_memory_specific_summary() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=240)
    obs = renderer.observation_summary(
        RenderEvent(
            channel="observation",
            node="action_results",
            step_id=0,
            payload={
                "action_results": [
                    ToolResult(
                        status="success",
                        output={
                            "status": "ok",
                            "revision": 7,
                            "slot": "construction_plan",
                            "section": "Mutation rationale",
                            "query": "length",
                            "available_sections": ["Candidate Lifecycle", "Mutation rationale"],
                            "content": "## Mutation rationale\n- poc.bin gdb_pending",
                        },
                        metadata={"tool_name": "RECALL"},
                    ).to_dict()
                ]
            },
        )
    )

    assert isinstance(obs, dict)
    assert obs["title"] == "RECALL Result"
    assert "slot=construction_plan" in obs["body"]
    assert "section=Mutation rationale" in obs["body"]
    assert "Candidate Lifecycle" in obs["body"]


def test_bash_and_gdb_outputs_keep_head_tail_and_identity() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=180)
    long_output = "A" * 300 + "\nIMPORTANT_TAIL"
    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                ToolResult(
                    status="success",
                    output={
                        "command": "python3 generate.py",
                        "returncode": 0,
                        "stdout": long_output,
                        "full_output_path": ".cybergym/raw/bash_1.log",
                    },
                    metadata={"tool_name": "BASH"},
                ).to_dict(),
                ToolResult(
                    status="success",
                    output={
                        "binary_path": "/out/fuzzer",
                        "commands": ["run", "bt"],
                        "returncode": 0,
                        "output": "gdb output",
                    },
                    metadata={"tool_name": "GDB"},
                ).to_dict(),
            ]
        },
    )

    obs = renderer.observation_summary(obs_evt)

    assert isinstance(obs, dict)
    assert obs["title"].startswith("BASH")
    assert "IMPORTANT_TAIL" in obs["body"]
    assert ".cybergym/raw/bash_1.log" in obs["body"]
    assert isinstance(obs.get("all_observations"), list)
    assert any(item.get("title") == "GDB Result" for item in obs["all_observations"])


def test_content_first_renderer_prioritizes_terminal_observation() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                {"status": "success", "path": ".", "count": 4},
                {
                    "env": {
                        "observation": {
                            "data": {
                                "terminal": {
                                    "output": "New Terminal Output:\napp.py\nrequirements.txt\n$ ",
                                    "screen": "Current Terminal Screen:\napp.py\nrequirements.txt\n$ ",
                                }
                            }
                        }
                    }
                },
            ]
        },
    )
    obs = renderer.observation_summary(obs_evt)
    assert isinstance(obs, dict)
    assert obs.get("title") == "Terminal Output"
    assert "app.py" in str(obs.get("body"))
    assert str(obs.get("primary_kind")) == "terminal_output"
    assert isinstance(obs.get("secondary"), dict)
    assert obs["secondary"]["title"] == "Tool Observation"


def test_claude_style_hook_shows_context_state_without_dumping_messages() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)

    hook.on_render_event(
        RenderEvent(
            channel="lifecycle",
            node="run_start",
            step_id=0,
            payload={"task": "demo task", "max_steps": 3},
        )
    )
    hook.on_render_event(
        RenderEvent(channel="lifecycle", node="step_start", step_id=0, payload={})
    )
    hook.on_render_event(
        RenderEvent(
            channel="observation",
            node="observation",
            step_id=0,
            payload={
                "observation": {
                    "scratchpad": ["a", "b"],
                    "memory": {"records": [1, 2, 3]},
                }
            },
        )
    )
    hook.on_render_event(
        RenderEvent(
            channel="thinking",
            node="model_input",
            step_id=0,
            payload={
                "messages": [{"role": "user", "content": "very long history"}],
                "model_input_digest": {
                    "message_count": 2,
                    "role_counts": {"system": 1, "user": 1},
                    "tool_call_count": 0,
                    "messages_hash": "abc123",
                    "sections": {"runtime_context": True, "runtime_reminder": True},
                    "sidecar_path": "/tmp/run/agent_steps/step-0000/assembled_messages.json",
                },
                "context": {
                    "input_tokens_total": 512,
                    "occupancy_ratio": 0.42,
                    "history_tokens": 320,
                    "output_tokens": 0,
                },
            },
        )
    )
    hook.on_render_event(
        RenderEvent(
            channel="thinking",
            node="decision",
            step_id=0,
            payload={"rationale": "I should use web_search first."},
        )
    )

    text = hook.console.export_text()
    assert "State" in text
    assert "ctx_used" in text
    assert "ctx_pct" in text
    assert "Context digest" in text
    assert "hash=abc123" in text
    assert "⦿" in text
    assert "web_search first" in text
    assert "very long history" not in text


def test_claude_style_hook_shows_runtime_context_only_when_explicitly_provided() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)

    hook.on_render_event(
        RenderEvent(channel="lifecycle", node="run_start", step_id=0, payload={})
    )
    hook.on_render_event(
        RenderEvent(channel="lifecycle", node="step_start", step_id=0, payload={})
    )
    hook.on_render_event(
        RenderEvent(
            channel="thinking",
            node="model_input",
            step_id=0,
            payload={
                "runtime_context_delivery": {"effective": "merge_tool"},
                "runtime_context_display": {
                    "tool_call_id": "call_1",
                    "content": "<RUNTIME_CONTEXT>fresh state</RUNTIME_CONTEXT>",
                },
            },
        )
    )

    text = hook.console.export_text()
    assert "folded into tool call_1" in text
    assert "fresh state" in text


def test_claude_style_hook_does_not_duplicate_successful_tool_invocations() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)
    hook.on_render_event(
        RenderEvent(
            channel="action",
            node="planned_actions",
            step_id=0,
            payload={"actions": [Action(name="READ", args={"path": "file.c"})]},
        )
    )
    hook.on_render_event(
        RenderEvent(
            channel="action",
            node="tool_invocations",
            step_id=0,
            payload={"tool_invocations": [{"tool_name": "READ", "status": "success"}]},
        )
    )

    text = hook.console.export_text()
    assert text.count("READ") == 1
    assert "Action(" not in text


def test_claude_style_hook_preserves_recoverable_tool_error_card() -> None:
    """A failed tool must show its recovery Card instead of only an Error title."""
    hook = ClaudeStyleHook(max_preview_chars=400)
    hook.console = Console(record=True, width=140)
    hook.on_render_event(
        RenderEvent(
            channel="observation",
            node="action_results",
            step_id=2,
            payload={
                "action_results": [
                    ToolResult(
                        status="error",
                        error="The cursor does not match this query or its snapshot is unavailable.",
                        output=(
                            "[GREP:invalid_cursor]\n\n"
                            "Code: `INVALID_CURSOR`\n"
                            "The cursor does not match this query or its snapshot is unavailable.\n\n"
                            "Retry: GREP(pattern=\"parse_record\", path=\"repo-vul/src\")"
                        ),
                        metadata={"tool_name": "GREP"},
                    ).to_dict()
                ]
            },
        )
    )

    text = hook.console.export_text()
    assert "Error: GREP Result" in text
    assert "[GREP:invalid_cursor]" in text
    assert "INVALID_CURSOR" in text
    assert "Retry: GREP" in text


def test_claude_style_hook_prints_full_action_arguments_without_false_parallel() -> None:
    hook = ClaudeStyleHook(max_preview_chars=20)
    hook.console = Console(record=True, width=180)
    content = "line one\n" + ("x" * 300) + "\nline three"
    hook.on_render_event(
        RenderEvent(
            channel="action",
            node="planned_actions",
            step_id=2,
            payload={
                "actions": [
                    Action(name="WRITE", action_id="call_write", args={"path": "repo-vul/poc.bin", "content": content}),
                    Action(name="BASH", action_id="call_bash", args={"command": "python3 build.py --input repo-vul/poc.bin"}),
                ]
            },
        )
    )

    text = hook.console.export_text()
    assert "2 ACTIONS" in text
    assert "IN PARALLEL" not in text
    assert "Action WRITE" in text and "Action BASH" in text
    assert "path=repo-vul/poc.bin" in text
    assert "line three" in text
    assert "python3 build.py --input repo-vul/poc.bin" in text


def test_renderer_suppresses_choice_repr_thought() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    thought = renderer.thought_text(
        RenderEvent(
            channel="thinking",
            node="model_output",
            step_id=0,
            payload={"raw_output": "Choice(finish_reason='tool_calls', message=...)"},
        )
    )

    assert thought is None


def test_model_runtime_digest_matches_actual_messages(tmp_path) -> None:
    runtime = _ModelRuntime(SimpleNamespace())
    state = SimpleNamespace(metadata={"trace_run_dir": str(tmp_path)})
    messages = [
        {"role": "system", "content": "# CyberGym Agent Contract\n<runtime_context>x</runtime_context>"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "READ", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "name": "READ", "tool_call_id": "call_1", "content": "ok"},
    ]

    digest1 = runtime._model_input_digest(state, 0, messages)
    digest2 = runtime._model_input_digest(state, 0, list(messages))

    assert digest1["messages_hash"] == digest2["messages_hash"]
    assert digest1["message_count"] == 3
    assert digest1["role_counts"] == {"assistant": 1, "system": 1, "tool": 1}
    assert digest1["tool_call_count"] == 1
    assert digest1["sections"]["runtime_context"] is True
    assert digest1["sidecar_path"].endswith("assembled_messages.json")


def test_cybergym_memory_snapshot_summarizes_files(tmp_path) -> None:
    memory_dir = tmp_path / ".cybergym"
    memory_dir.mkdir()
    (memory_dir / "next_action.md").write_text(
        "# Next Action\n\n## Phase\nexploration\n\n## Required Action\n- Build hypothesis\n\n## Do Not Repeat\n- stale seed\n",
        encoding="utf-8",
    )
    (memory_dir / "program_model.md").write_text("# Program Model\n\nconfirmed facts\n", encoding="utf-8")
    (memory_dir / "hypothesis_pool.md").write_text("No active hypotheses\n", encoding="utf-8")
    (memory_dir / "experiment_ledger.md").write_text(
        "| ID | POC Path | SHA256 | Size | Result |\n"
        "|----|----------|--------|------|--------|\n"
        "| E001 | poc.bin | sha256:abc | 4 | vul_only_triggered |\n",
        encoding="utf-8",
    )
    (memory_dir / "gdb_observations.md").write_text("## G001\nFacts: reached sink\n", encoding="utf-8")
    hook = RenderStreamHook()

    snapshot = hook._cybergym_memory_snapshot(SimpleNamespace(workspace_root=str(tmp_path), current_phase="exploration"))
    summary = ContentFirstRenderer(max_preview_chars=300).memory_summary(
        RenderEvent(channel="memory", node="cybergym_memory", step_id=0, payload={"snapshot": snapshot})
    )

    assert snapshot["file_statuses"]["program_model.md"] == "ready"
    assert snapshot["file_statuses"]["hypothesis_pool.md"] == "template"
    assert "Build hypothesis" in snapshot["next_required"]
    assert "E001 poc.bin vul_only_triggered" in snapshot["ledger_recent"]
    assert isinstance(summary, str)
    assert "files=" in summary
    assert "ledger=E001 poc.bin vul_only_triggered" in summary


def test_content_first_renderer_extracts_parser_diagnostics() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    evt = RenderEvent(
        channel="parser",
        node="parser_diagnostics",
        step_id=0,
        payload={
            "diagnostics": {
                "parser": "TerminusJsonParser",
                "contract": "terminus_json_v1",
                "severity": "error",
                "code": "missing_required_field",
                "summary": "Missing required field: tools",
                "details": "Expected one of commands, tools, or task_complete=true.",
                "extraction_mode": "extracted",
                "repair_instruction": "Return valid JSON with analysis, plan, and either commands, tools, or task_complete=true.",
                "raw_output_preview": '{"analysis":"x","plan":"y"}',
            }
        },
    )
    diag = renderer.parser_diagnostic_summary(evt)
    assert isinstance(diag, dict)
    assert diag.get("code") == "missing_required_field"
    assert diag.get("extraction_mode") == "extracted"
    assert "Return valid JSON" in str(diag.get("repair_instruction"))


def test_claude_style_hook_renders_parser_diagnostics() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)
    hook.on_render_event(
        RenderEvent(
            channel="parser",
            node="parser_diagnostics",
            step_id=0,
            payload={
                "diagnostics": {
                    "parser": "TerminusJsonParser",
                    "contract": "terminus_json_v1",
                    "severity": "error",
                    "code": "missing_required_field",
                    "summary": "Missing required field: tools",
                    "details": "Expected one of commands, tools, or task_complete=true.",
                    "extraction_mode": "extracted",
                    "repair_instruction": "Return valid JSON with analysis, plan, and either commands, tools, or task_complete=true.",
                    "raw_output_preview": '{"analysis":"x","plan":"y"}',
                }
            },
        )
    )
    text = hook.console.export_text()
    assert "PARSER ERROR" in text
    assert "missing_required_field" in text
    assert "Missing required field: tools" in text
    assert "Extraction:" in text
    assert "extracted" in text


def test_claude_style_hook_hides_salvaged_parser_warnings_by_default() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)
    hook.on_render_event(
        RenderEvent(
            channel="parser",
            node="parser_result",
            step_id=0,
            payload={
                "has_diagnostics": True,
                "severity": "warning",
                "salvage_applied": True,
            },
        )
    )
    hook.on_render_event(
        RenderEvent(
            channel="parser",
            node="parser_diagnostics",
            step_id=0,
            payload={
                "diagnostics": {
                    "parser": "TerminusJsonParser",
                    "contract": "terminus_json_v1",
                    "severity": "warning",
                    "code": "salvaged_json_payload",
                    "summary": "Parser warnings were recorded while reading Terminus JSON output.",
                    "details": "AUTO-CORRECTED: extracted a JSON-like object from surrounding text.",
                    "extraction_mode": "extracted",
                    "salvage_applied": True,
                    "salvage_summary": "AUTO-CORRECTED: extracted a JSON-like object from surrounding text.",
                }
            },
        )
    )
    text = hook.console.export_text()
    assert "PARSER WARNING" not in text
    assert "repairing output contract" not in text.lower()


def test_claude_style_hook_prints_agent_composition() -> None:
    class _Budget:
        max_steps = 5

    class _LLM:
        model_name = "Qwen/Qwen3-8B"

    class _Agent:
        llm = _LLM()

    class _Memory:
        pass

    class _Search:
        pass

    class _Registry:
        @staticmethod
        def list_tools():
            return ["web_search", "visit_url"]

    class _Engine:
        budget = _Budget()
        agent = _Agent()
        memory = _Memory()
        search = _Search()
        tool_registry = _Registry()

    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=120)
    hook.on_run_start(task="demo", state={}, engine=_Engine())
    hook._stop_status()
    text = hook.console.export_text()
    assert "AGENT COMPOSITION" in text
    assert "Qwen/Qwen3-8B" in text
    assert "web_search" in text
