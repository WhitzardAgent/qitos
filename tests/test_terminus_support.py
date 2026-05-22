from __future__ import annotations

from pathlib import Path
from typing import List

from examples.real.terminus_2 import Terminus2Agent
from qitos.core import HistoryMessage, TerminalCapability
from qitos.kit import (
    SendTerminalKeys,
    TerminusJsonParser,
    TerminusXmlParser,
    TokenBudgetSummaryHistory,
    TmuxEnv,
)


class FakeTerminal(TerminalCapability):
    def __init__(self):
        self.alive = True
        self.screen = "$ "
        self.buffer = "$ "
        self.previous = None
        self.sent: list[str] = []
        self.waits: list[float] = []
        self.reset_calls = 0
        self.closed = False
        self.ts = 0.0

    def reset_session(self, cwd: str | None = None) -> None:
        self.reset_calls += 1
        self.screen = "$ "
        self.buffer = "$ "
        self.previous = None
        self.alive = True

    def close_session(self) -> None:
        self.closed = True
        self.alive = False

    def send_keys(
        self,
        keys: str | list[str],
        min_timeout_sec: float = 0.0,
        block: bool = False,
        max_timeout_sec: float = 180.0,
    ) -> dict:
        text = "".join(keys) if isinstance(keys, list) else str(keys)
        self.sent.append(text)
        self.waits.append(float(min_timeout_sec))
        self.ts += 1.0
        if text.strip() == "pwd":
            update = "/workspace\n$ "
        elif "ls" in text:
            update = "README.txt\nnotes.txt\n$ "
        elif not text:
            update = self.screen
        else:
            update = f"executed: {text.strip()}\n$ "
        self.buffer += update
        self.screen = update
        return {
            "status": "success",
            "keys": text,
            "waited_seconds": min_timeout_sec,
            "block": block,
        }

    def capture_screen(self) -> str:
        return self.screen

    def capture_buffer(self) -> str:
        return self.buffer

    def get_incremental_output(self) -> str:
        current = self.buffer
        if self.previous is None:
            self.previous = current
            return f"Current Terminal Screen:\n{self.screen}"
        if self.previous in current:
            idx = current.index(self.previous) + len(self.previous)
            delta = current[idx:].lstrip("\n")
        else:
            delta = self.screen
        self.previous = current
        if delta.strip():
            return f"New Terminal Output:\n{delta}"
        return f"Current Terminal Screen:\n{self.screen}"

    def is_session_alive(self) -> bool:
        return self.alive

    def get_timestamp(self) -> float | None:
        return self.ts


class DummyModel:
    def __init__(self, outputs: List[str]):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, str]]] = []
        self.model = "dummy-terminus"

    def __call__(self, messages):
        self.calls.append(list(messages))
        return self.outputs.pop(0)


def test_send_terminal_keys_tool_uses_terminal_ops() -> None:
    terminal = FakeTerminal()
    tool = SendTerminalKeys()
    result = tool.run(
        "ls\n", duration_sec=0.25, runtime_context={"ops": {"terminal": terminal}}
    )
    assert result["status"] == "success"
    assert terminal.sent == ["ls\n"]
    assert terminal.waits == [0.25]


def test_send_terminal_keys_submit_appends_newline_once() -> None:
    terminal = FakeTerminal()
    tool = SendTerminalKeys()
    result = tool.execute(
        {
            "keystrokes": "pwd",
            "duration_sec": 0.1,
            "submit": True,
        },
        runtime_context={"ops": {"terminal": terminal}},
    )
    assert result["status"] == "success"
    assert result["submit"] is True
    assert terminal.sent == ["pwd\n"]


def test_tmux_env_can_wrap_custom_terminal_backend(tmp_path: Path) -> None:
    terminal = FakeTerminal()
    env = TmuxEnv(
        workspace_root=str(tmp_path),
        session_name="test-terminus",
        terminal=terminal,
        auto_kill=False,
    )
    obs = env.reset(workspace=str(tmp_path))
    terminal_payload = obs.data["terminal"]
    assert terminal_payload["backend"] == "tmux"
    assert terminal_payload["session_alive"] is True
    step = env.step({"name": "send_terminal_keys"})
    assert step.done is False
    env.teardown()
    assert terminal.closed is True


def test_terminus_json_parser_handles_actions_completion_and_feedback() -> None:
    parser = TerminusJsonParser()
    act = parser.parse(
        '{"analysis":"check state","plan":"list files","commands":[{"keystrokes":"ls\\n","duration":0.1}]}'
    )
    assert act.mode == "act"
    assert act.actions[0]["name"] == "send_terminal_keys"
    assert act.meta["plan"] == "list files"

    complete = parser.parse(
        '{"analysis":"done","plan":"finish","commands":[],"task_complete":true}'
    )
    assert complete.mode == "wait"
    assert complete.meta["task_complete_requested"] is True

    tool_act = parser.parse(
        '{"analysis":"inventory repo","plan":"use audit tools","tools":[{"name":"audit_inventory","args":{}},{"name":"grep_files","args":{"pattern":"SECRET_KEY"}}]}'
    )
    assert tool_act.mode == "act"
    assert [item["name"] for item in tool_act.actions] == [
        "audit_inventory",
        "grep_files",
    ]

    wrapped = parser.parse(
        """I found the next step:

```json
{'analysis': 'inspect files', 'plan': 'use audit inventory', 'tools': [{'name': 'audit_inventory', 'args': {}}]}
```

This should help.
"""
    )
    assert wrapped.mode == "act"
    assert wrapped.actions[0]["name"] == "audit_inventory"
    assert wrapped.meta["parser_diagnostics"]["severity"] == "warning"
    assert wrapped.meta["parser_diagnostics"]["salvage_applied"] is True
    assert wrapped.meta["parser_diagnostics"]["extraction_mode"] == "python_literal"

    largest = parser.parse(
        """Context before.
{"analysis":"small","plan":"skip","commands":[{"keystrokes":"pwd\\n","duration":0.1}]}
More notes.
{"analysis":"inspect files","plan":"use tools","tools":[{"name":"audit_inventory","args":{}},{"name":"grep_files","args":{"pattern":"SECRET_KEY","path_glob":"**/*"}}]}
Context after.
"""
    )
    assert largest.mode == "act"
    assert [item["name"] for item in largest.actions] == [
        "audit_inventory",
        "grep_files",
    ]
    assert largest.meta["parser_diagnostics"]["extraction_mode"] == "extracted"

    malformed = parser.parse('before {"analysis":"x"')
    assert malformed.mode == "wait"
    assert malformed.meta["parser_error"] is True
    assert malformed.meta["parser_diagnostics"]["code"] in {
        "invalid_json",
        "missing_required_field",
    }
    assert malformed.meta["parser_diagnostics"]["extraction_mode"] in {
        "extracted",
        "brace_fix",
    }
    assert "Return valid JSON" in malformed.meta["parser_feedback"]


def test_terminus_xml_parser_salvages_missing_response_close() -> None:
    parser = TerminusXmlParser()
    output = parser.parse(
        '<response><analysis>inspect</analysis><plan>run pwd</plan><commands><keystrokes duration="0.1">pwd\n</keystrokes></commands>'
    )
    assert output.mode == "act"
    assert output.actions[0]["args"]["keystrokes"].startswith("pwd")
    assert "AUTO-CORRECTED" in output.meta.get("parser_warning", "")

    tool_output = parser.parse(
        '<response><analysis>inspect</analysis><plan>audit</plan><tools><tool name="audit_inventory"></tool><tool name="read_file"><arg name="path">app.py</arg></tool></tools></response>'
    )
    assert tool_output.mode == "act"
    assert [item["name"] for item in tool_output.actions] == [
        "audit_inventory",
        "read_file",
    ]


def test_token_budget_history_summarizes_older_messages() -> None:
    history = TokenBudgetSummaryHistory(max_tokens=30, keep_last=2, hard_window=20)
    for idx in range(6):
        role = "user" if idx % 2 == 0 else "assistant"
        history.append(
            HistoryMessage(
                role=role,
                content=f"message {idx} with extra context to consume tokens",
                step_id=idx,
            )
        )
    retrieved = history.retrieve(
        query={
            "roles": ["user", "assistant"],
            "max_items": 6,
            "max_tokens": 30,
            "pending_content": "next prompt",
        }
    )
    assert len(retrieved) <= 3
    assert retrieved[0].metadata.get("summary") is True


def test_terminus_agent_roundtrip_uses_parser_feedback_and_double_confirmation(
    tmp_path: Path,
) -> None:
    llm = DummyModel(
        outputs=[
            "not valid json",
            '{"analysis":"Need to inspect files","plan":"Run ls","commands":[{"keystrokes":"ls\\n","duration":0.1}]}',
            '{"analysis":"The task looks complete","plan":"Finish","commands":[],"task_complete":true}',
            '{"analysis":"Confirmed completion","plan":"Finish","commands":[],"task_complete":true}',
        ]
    )
    terminal = FakeTerminal()
    env = TmuxEnv(
        workspace_root=str(tmp_path),
        session_name="terminus-loop",
        terminal=terminal,
        auto_kill=False,
    )
    agent = Terminus2Agent(llm=llm)

    result = agent.run(
        task="Inspect the workspace and summarize it.",
        workspace=str(tmp_path),
        env=env,
        protocol="terminus_json_v1",
        max_steps=8,
        parser_format="json",
        render=False,
        trace=False,
        return_state=True,
    )

    assert result.state.stop_reason in ("success", "final")
    assert result.state.final_result == "Confirmed completion"
    assert terminal.sent == ["ls\n"]
    assert len(llm.calls) == 4
    assert "Parser feedback from previous response" in llm.calls[1][-1]["content"]
    assert (
        "Are you sure you want to mark the task as complete?"
        in llm.calls[3][-1]["content"]
    )
