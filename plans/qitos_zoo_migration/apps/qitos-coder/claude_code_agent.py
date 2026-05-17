"""Claude Code-style coding agent built on the canonical coding tool preset.

NOTE: For the full Claude Code replication (30+ tools, permission pipeline,
streaming REPL, MCP, sub-agents, etc.), see ``examples/real/claude_code/``.
This file remains as a minimal preset-first example.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from qitos import Action, AgentModule, Decision, HistoryPolicy, RunSpec, StateSchema
from qitos.harness import build_harness_policy, build_model_for_preset, resolve_family_preset
from qitos.kit import ReActTextParser, format_action
from qitos.kit.toolset import coding_tools

TASK = "Fix buggy_module.py, keep a todo list, and verify the fix with the provided command."
WORKSPACE = Path("./playground/claude_code_agent")
DEFAULT_MODEL_FAMILY = "qwen"
DEFAULT_MODEL_NAME = "Qwen/Qwen3-8B"
DEFAULT_MODEL_BASE_URL = "https://api.siliconflow.cn/v1/"
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
- Only use `web_fetch` when the task needs documentation.

Preferred tool patterns:
- Inspection: `view`, `read_file`
- Editing: `str_replace`, `replace_lines`, `write_file`, `create`
- Search: `glob_files`, `grep_files`, `tool_search`
- Execution: `run_command`
- Planning/state: `todo_write`, `enter_plan_mode`, `exit_plan_mode`
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
    def __init__(
        self,
        llm: Any,
        workspace_root: str,
        *,
        model_parser: Any | None = None,
        model_protocol: Any | None = None,
    ):
        super().__init__(
            toolset=[
                coding_tools(
                    workspace_root=workspace_root,
                    shell_timeout=30,
                    include_notebook=True,
                )
            ],
            llm=llm,
            model_parser=model_parser or ReActTextParser(),
            model_protocol=model_protocol,
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
        _ = state
        return self.compose_system_prompt(SYSTEM_PROMPT)

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


def _family_default_model_name(family_id: str) -> str:
    try:
        preset = resolve_family_preset(family_id)
    except ValueError:
        return DEFAULT_MODEL_NAME
    if preset.recommended_models:
        return str(preset.recommended_models[0])
    return DEFAULT_MODEL_NAME


def _family_default_base_url(family_id: str) -> str:
    normalized = str(family_id or "").strip().lower()
    if normalized == "kimi":
        return "https://api.moonshot.ai/v1"
    if normalized == "minimax":
        return "https://api.minimax.chat/v1"
    return DEFAULT_MODEL_BASE_URL


def _resolve_runtime_config(
    args: argparse.Namespace | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str | None]:
    env_map = env if env is not None else os.environ
    cli_family = str(getattr(args, "model_family", "") or "").strip() or None
    env_family = str(env_map.get("QITOS_MODEL_FAMILY", "") or "").strip() or None
    family_id = cli_family or env_family

    cli_model = str(getattr(args, "model_name", "") or "").strip() or None
    env_model = str(env_map.get("QITOS_MODEL", "") or "").strip() or None
    model_name = cli_model or env_model

    resolved_family = family_id
    if not resolved_family and model_name:
        resolved_family = resolve_family_preset(model_name).id
    if not resolved_family:
        resolved_family = DEFAULT_MODEL_FAMILY
    if not model_name:
        model_name = _family_default_model_name(resolved_family)

    cli_base_url = str(getattr(args, "base_url", "") or "").strip() or None
    env_base_url = str(env_map.get("OPENAI_BASE_URL", "") or "").strip() or None
    base_url = cli_base_url or env_base_url or _family_default_base_url(resolved_family)

    cli_api_key = str(getattr(args, "api_key", "") or "").strip() or None
    env_api_key = (
        str(env_map.get("OPENAI_API_KEY", "") or "").strip()
        or str(env_map.get("QITOS_API_KEY", "") or "").strip()
        or None
    )
    api_key = cli_api_key or env_api_key

    cli_protocol = str(getattr(args, "protocol", "") or "").strip() or None
    env_protocol = str(env_map.get("QITOS_PROTOCOL", "") or "").strip() or None
    protocol = cli_protocol or env_protocol or None

    return {
        "model_family": resolved_family,
        "model_name": model_name,
        "base_url": base_url,
        "api_key": api_key,
        "protocol": protocol,
    }


def build_model(
    *,
    model_family: str,
    model_name: str,
    base_url: str,
    api_key: str | None,
    protocol: str | None = None,
) -> Any:
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return build_model_for_preset(
        family_id=model_family,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        protocol=protocol,
        temperature=0.2,
        max_tokens=2048,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Claude Code-style coding agent with QitOS family presets"
    )
    parser.add_argument("--model-family")
    parser.add_argument("--model-name")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--protocol")
    parser.add_argument("--workspace", default=str(WORKSPACE))
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--print-harness", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_arg_parser().parse_args(argv)
    config = _resolve_runtime_config(args)
    workspace = Path(str(args.workspace)).resolve()
    harness = build_harness_policy(
        model_name=str(config["model_name"]),
        family_id=str(config["model_family"]),
        protocol=config["protocol"],
        resolution_source="claude_code_agent",
    )
    llm = build_model(
        model_family=str(config["model_family"]),
        model_name=str(config["model_name"]),
        base_url=str(config["base_url"]),
        api_key=str(config["api_key"]) if config["api_key"] else None,
        protocol=str(config["protocol"]) if config["protocol"] else None,
    )
    run_spec = RunSpec.infer(
        model_name=str(config["model_name"]),
        prompt_protocol=harness.protocol.id,
        parser_name=harness.parser_name,
        toolset_name="coding_tools",
        environment={"base_url": str(config["base_url"]), "workspace": str(workspace)},
        metadata={
            "family_preset": harness.family_preset.id,
            "harness_policy": harness.to_dict(),
            "tool_policy": harness.tool_policy.to_dict(),
            "context_policy": harness.context_policy.to_dict(),
        },
    )

    if args.print_harness:
        print("family_preset:", harness.family_preset.id)
        print("model_name:", config["model_name"])
        print("base_url:", config["base_url"])
        print("protocol:", harness.protocol.id)
        print("parser:", harness.parser_name)
        print("tool_delivery:", harness.tool_policy.primary_delivery)
        print("native_tool_call_preferred:", harness.tool_policy.native_tool_call_preferred)
        print(
            "decision_lane_preference:",
            "native_tool_calls"
            if harness.tool_policy.native_tool_call_preferred
            else "parser",
        )
        print("context_window_hint:", harness.context_policy.context_window_hint)

    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / TARGET_FILE
    if not target.exists():
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    agent = ClaudeCodeAgent(
        llm=llm,
        workspace_root=str(workspace),
        model_parser=harness.parser,
        model_protocol=harness.protocol,
    )
    result = agent.run(
        task=str(args.task),
        workspace=str(workspace),
        target_file=TARGET_FILE,
        test_command=TEST_COMMAND,
        doc_url=DOC_URL,
        max_steps=int(args.max_steps),
        history_policy=HistoryPolicy(max_messages=16, max_tokens=2800),
        run_spec=run_spec,
        return_state=True,
    )

    print("workspace:", workspace)
    print("family_preset:", harness.family_preset.id)
    print("model_name:", config["model_name"])
    print("protocol:", harness.protocol.id)
    print("parser:", harness.parser_name)
    print("tool_delivery:", harness.tool_policy.primary_delivery)
    print("native_tool_call_preferred:", harness.tool_policy.native_tool_call_preferred)
    print("final_result:", result.state.final_result)
    print("todos:", result.state.todos)
    print("mode:", result.state.mode)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
