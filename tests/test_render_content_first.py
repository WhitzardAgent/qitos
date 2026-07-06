from __future__ import annotations

from rich.console import Console

from qitos.render.content_renderer import ContentFirstRenderer
from qitos.render.events import RenderEvent
from qitos.render.hooks import ClaudeStyleHook


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


def test_content_first_renderer_renders_nested_tool_output_readably() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                {
                    "status": "success",
                    "output": {
                        "status": "success",
                        "poc_path": "/tmp/poc",
                        "returncode": 0,
                        "commands": ["break main", "run /tmp/poc", "bt"],
                        "output": (
                            "Breakpoint 1, main () at harness.cc:12\n"
                            "Runtime evidence: sink reached\n"
                            "Inferior 1 exited normally"
                        ),
                    },
                    "error": None,
                    "metadata": {"tool_name": "gdb_debug", "attempts": 1},
                }
            ]
        },
    )

    obs = renderer.observation_summary(obs_evt)

    assert isinstance(obs, dict)
    assert obs.get("title") == "gdb_debug"
    body = str(obs.get("body"))
    assert "tool_name=gdb_debug" in body
    assert "poc_path=/tmp/poc" in body
    assert "returncode=0" in body
    assert "Commands:" in body
    assert "  - break main" in body
    assert "Output:" in body
    assert "Breakpoint 1, main () at harness.cc:12" in body
    assert "Runtime evidence: sink reached" in body
    assert '"output"' not in body
    assert "{\n" not in body


def test_content_first_renderer_deduplicates_promoted_string_output() -> None:
    renderer = ContentFirstRenderer(max_preview_chars=200)
    obs_evt = RenderEvent(
        channel="observation",
        node="action_results",
        step_id=0,
        payload={
            "action_results": [
                {
                    "status": "success",
                    "output": "same visible text\nsecond line",
                    "content": "same visible text\nsecond line",
                    "metadata": {"tool_name": "run_candidate"},
                }
            ]
        },
    )

    obs = renderer.observation_summary(obs_evt)

    assert isinstance(obs, dict)
    body = str(obs.get("body"))
    assert body.count("same visible text") == 1
    assert "Output:" in body
    assert "Content:" not in body


def test_claude_style_hook_renders_nested_tool_output_readably() -> None:
    hook = ClaudeStyleHook(max_preview_chars=200)
    hook.console = Console(record=True, width=140)
    hook.on_render_event(
        RenderEvent(
            channel="observation",
            node="action_results",
            step_id=0,
            payload={
                "action_results": [
                    {
                        "status": "success",
                        "output": {
                            "status": "success",
                            "poc_path": "/tmp/poc",
                            "returncode": 0,
                            "commands": ["break main", "run /tmp/poc"],
                            "output": (
                                "Breakpoint 1, main () at harness.cc:12\n"
                                "Runtime evidence: sink reached"
                            ),
                        },
                        "metadata": {"tool_name": "gdb_debug", "attempts": 1},
                    }
                ]
            },
        )
    )

    text = hook.console.export_text()

    assert "Observation Title: gdb_debug" in text
    assert "tool_name=gdb_debug" in text
    assert "poc_path=/tmp/poc" in text
    assert "Commands:" in text
    assert "  - break main" in text
    assert "Output:" in text
    assert "Runtime evidence: sink reached" in text
    assert '"output"' not in text
    assert "{\n" not in text


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
    assert "⦿" in text
    assert "web_search first" in text
    assert "very long history" not in text


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
