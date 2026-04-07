"""Claude Code-style coding agent built on the canonical coding tool preset."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, HistoryPolicy, StateSchema
from qitos.kit import ReActTextParser, format_action, render_prompt
from qitos.kit.toolset import coding_tools
from qitos.models import OpenAICompatibleModel

TASK = "Fix buggy_module.py, keep a todo list, and verify the fix with the provided command."
WORKSPACE = Path("./playground/claude_code_agent")
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 12
TARGET_FILE = "buggy_module.py"
TEST_COMMAND = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'
DOC_URL = os.getenv("QITOS_CLAUDE_CODE_DOC_URL", "").strip()

SYSTEM_PROMPT = """You are a Claude Code-style coding agent.

Workflow:
- Start by writing a todo list with `todo_write`.
- If you are unsure which tool to use, call `tool_search`.
- Read before you edit.
- Make the smallest correct change.
- Run verification immediately after editing.
- Only use `web_fetch` or `web_fetch_v2` when the task needs documentation.

Preferred tool patterns:
- Inspection: `view`, `read_file`, `file_read_v2`
- Editing: `str_replace`, `replace_lines`, `file_edit_v2`
- Search: `glob_files`, `grep_files`, `tool_search`
- Execution: `run_command` or `bash_v2`
- Planning/state: `todo_write`, `enter_plan_mode`, `exit_plan_mode`

Available tools:
{tool_schema}

Return format (strict):
Thought: <short reasoning>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <what changed + verification proof>
"""


@dataclass
class ClaudeCodeState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    target_file: str = TARGET_FILE
    test_command: str = TEST_COMMAND
    doc_url: str = DOC_URL
    mode: str = "work"


class ClaudeCodeAgent(AgentModule[ClaudeCodeState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        super().__init__(
            toolset=[
                coding_tools(
                    workspace_root=workspace_root,
                    shell_timeout=30,
                    include_notebook=True,
                )
            ],
            llm=llm,
            model_parser=ReActTextParser(),
        )

    def init_state(self, task: str, **kwargs: Any) -> ClaudeCodeState:
        return ClaudeCodeState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            target_file=str(kwargs.get("target_file", TARGET_FILE)),
            test_command=str(kwargs.get("test_command", TEST_COMMAND)),
            doc_url=str(kwargs.get("doc_url", DOC_URL)),
        )

    def build_system_prompt(self, state: ClaudeCodeState) -> str | None:
        return render_prompt(
            SYSTEM_PROMPT, {"tool_schema": self.tool_registry.get_tool_descriptions()}
        )

    def prepare(self, state: ClaudeCodeState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Verification command: {state.test_command}",
            f"Mode: {state.mode}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.doc_url:
            lines.append(f"Optional documentation URL: {state.doc_url}")
        if state.todos:
            lines.append("Current todos:")
            for item in state.todos:
                lines.append(
                    f"- {item.get('content', '')} [{item.get('status', 'pending')}]"
                )
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-10:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ClaudeCodeState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ClaudeCodeState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = action_results[0]
            state.scratchpad.append(f"Observation: {first}")
            if isinstance(first, dict):
                if first.get("todos"):
                    state.todos = list(first.get("todos") or [])
                if first.get("current_mode"):
                    state.mode = str(first.get("current_mode"))
                if int(first.get("returncode", 1)) == 0:
                    state.final_result = (
                        "Verification passed with the canonical coding toolset."
                    )
            if state.metadata.get("todos"):
                state.todos = list(state.metadata.get("todos") or [])
            if state.metadata.get("mode"):
                state.mode = str(state.metadata.get("mode"))
        state.scratchpad = state.scratchpad[-40:]
        return state


def build_model() -> OpenAICompatibleModel:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.2,
        max_tokens=2048,
    )


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    target = WORKSPACE / TARGET_FILE
    if not target.exists():
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    agent = ClaudeCodeAgent(llm=build_model(), workspace_root=str(WORKSPACE))
    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        target_file=TARGET_FILE,
        test_command=TEST_COMMAND,
        doc_url=DOC_URL,
        max_steps=MAX_STEPS,
        history_policy=HistoryPolicy(max_messages=16, max_tokens=2800),
        return_state=True,
    )

    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("todos:", result.state.todos)
    print("mode:", result.state.mode)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
