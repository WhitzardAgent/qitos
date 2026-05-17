"""Faithful Terminus-2-style terminal agent built with QiTOS."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from qitos import AgentModule, Decision, StateSchema, StopReason, ToolRegistry
from qitos.kit import (
    SendTerminalKeys,
    TokenBudgetSummaryHistory,
    TmuxEnv,
)
from qitos.models import OpenAICompatibleModel

TASK = "Inspect this workspace with terminal commands, determine whether todo.txt exists, and summarize what notes.txt contains."
WORKSPACE = Path("../../playground/terminus_2")
SESSION_NAME = "qitos_terminus_2"
PARSER_FORMAT = os.getenv("QITOS_TERMINUS_FORMAT", "").strip().lower()
MODEL_NAME = os.getenv("QITOS_MODEL", "MiniMax-M2.5")
MODEL_BASE_URL = os.getenv(
    "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
MAX_STEPS = 20
MAX_TERMINAL_BYTES = 10000

TERMINUS_BASE_PROMPT = """You are an AI assistant solving command-line tasks in a Linux terminal.

You will be given:
- the task description
- the latest terminal state
- optional parser feedback from the previous response

Your job is to control the terminal safely and efficiently.

Rules:
- Prefer short, incremental commands.
- Use only the active protocol format when calling tools or marking completion.
- If the task is complete, signal completion in the active protocol format.
"""


@dataclass
class TerminusState(StateSchema):
    parser_format: str = PARSER_FORMAT
    terminal_output: str = ""
    terminal_screen: str = ""
    parser_feedback: str = ""
    timeout_feedback: str = ""
    pending_completion: bool = False
    last_analysis: str = ""
    last_plan: str = ""
    markers: list[dict[str, Any]] = field(default_factory=list)


class Terminus2Agent(AgentModule[TerminusState, dict[str, Any], dict[str, Any]]):
    name = "terminus_2"

    def __init__(self, llm: Any):
        registry = ToolRegistry()
        registry.register(SendTerminalKeys())
        protocol_override = None
        if PARSER_FORMAT == "json":
            protocol_override = "terminus_json_v1"
        elif PARSER_FORMAT == "xml":
            protocol_override = "terminus_xml_v1"
        history = TokenBudgetSummaryHistory(
            llm=llm, max_tokens=12000, keep_last=8, hard_window=64
        )
        super().__init__(
            tool_registry=registry,
            llm=llm,
            model_protocol=protocol_override,
            history=history,
        )

    def init_state(self, task: str, **kwargs: Any) -> TerminusState:
        return TerminusState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            parser_format=str(kwargs.get("parser_format", PARSER_FORMAT)),
        )

    def base_persona_prompt(self, state: TerminusState) -> str:
        _ = state
        return TERMINUS_BASE_PROMPT

    def prepare(self, state: TerminusState) -> str:
        observation = getattr(self, "_runtime_observation", None)
        terminal = self._extract_terminal_payload(observation) or {
            "output": state.terminal_output,
            "screen": state.terminal_screen,
            "timestamp": None,
        }
        terminal_output = self._limit_output_length(
            terminal.get("output") or terminal.get("screen") or state.terminal_output
        )
        terminal_screen = self._limit_output_length(
            terminal.get("screen") or terminal_output
        )

        if state.pending_completion:
            confirmation_target = terminal_output or terminal_screen
            return (
                f"Current terminal state:\n{confirmation_target}\n\n"
                "Are you sure you want to mark the task as complete? This will end the run. "
                "If so, emit the completion flag again in the exact required output format."
            )

        lines = [
            f"Task Description:\n{state.task}",
            "",
            f"Current terminal state:\n{terminal_output or terminal_screen}",
        ]
        if state.last_plan:
            lines.extend(["", f"Previous plan:\n{state.last_plan}"])
        return "\n".join(lines)

    def reduce(
        self,
        state: TerminusState,
        observation: dict[str, Any],
        decision: Decision[dict[str, Any]],
    ) -> TerminusState:
        terminal = self._extract_terminal_payload(observation) or {}
        latest_output = str(terminal.get("output") or terminal.get("screen") or "")
        latest_screen = str(terminal.get("screen") or latest_output)
        state.terminal_output = self._limit_output_length(latest_output)
        state.terminal_screen = self._limit_output_length(latest_screen)

        meta = decision.meta if isinstance(decision.meta, dict) else {}
        state.last_analysis = str(
            meta.get("analysis") or decision.rationale or state.last_analysis
        )
        state.last_plan = str(meta.get("plan") or state.last_plan)

        parser_feedback = str(meta.get("parser_feedback") or "").strip()
        parser_warning = str(meta.get("parser_warning") or "").strip()
        state.timeout_feedback = self._extract_timeout_feedback(observation)

        if meta.get("parser_error"):
            state.parser_feedback = parser_feedback or parser_warning
            state.pending_completion = False
        else:
            state.parser_feedback = parser_warning

        if meta.get("task_complete_requested"):
            if state.pending_completion:
                final_result = (
                    state.last_analysis or "Task marked complete from terminal state."
                )
                state.set_stop(StopReason.SUCCESS, final_result=final_result)
            else:
                state.pending_completion = True
                state.parser_feedback = ""
        elif decision.mode == "act":
            state.pending_completion = False

        state.markers.append(
            {
                "step": state.current_step,
                "timestamp": terminal.get("timestamp"),
                "session_alive": terminal.get("session_alive"),
                "analysis": state.last_analysis,
                "plan": state.last_plan,
            }
        )
        state.markers = state.markers[-100:]
        return state

    def should_stop(self, state: TerminusState) -> bool:
        return bool(state.stop_reason)

    def _extract_terminal_payload(self, observation: Any) -> Dict[str, Any]:
        if not isinstance(observation, dict):
            return {}
        env_payload = observation.get("env")
        if not isinstance(env_payload, dict):
            return {}
        env_observation = env_payload.get("observation")
        if not isinstance(env_observation, dict):
            return {}
        data = env_observation.get("data")
        if not isinstance(data, dict):
            return {}
        terminal = data.get("terminal")
        return terminal if isinstance(terminal, dict) else {}

    def _limit_output_length(
        self, output: str, max_bytes: int = MAX_TERMINAL_BYTES
    ) -> str:
        encoded = output.encode("utf-8", errors="ignore")
        if len(encoded) <= max_bytes:
            return output
        portion = max_bytes // 2
        first = encoded[:portion].decode("utf-8", errors="ignore")
        last = encoded[-portion:].decode("utf-8", errors="ignore")
        omitted = len(encoded) - len(first.encode("utf-8")) - len(last.encode("utf-8"))
        return f"{first}\n[... output limited to {max_bytes} bytes; {omitted} interior bytes omitted ...]\n{last}"

    def _extract_timeout_feedback(self, observation: dict[str, Any]) -> str:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        for item in action_results:
            if isinstance(item, dict):
                message = str(item.get("error") or item.get("message") or "")
                if "timed out" in message.lower():
                    return message
        return ""


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


def bootstrap_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text(
        "QiTOS terminal demo workspace.\nThe project goal is to build a modular agent framework.\n",
        encoding="utf-8",
    )
    (root / "README.txt").write_text(
        "Use terminal commands to inspect this folder.\n", encoding="utf-8"
    )


def main() -> None:
    bootstrap_workspace(WORKSPACE)
    env = TmuxEnv(
        workspace_root=str(WORKSPACE), session_name=SESSION_NAME, auto_kill=True
    )
    agent = Terminus2Agent(llm=build_model())
    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        env=env,
        max_steps=MAX_STEPS,
        parser_format=PARSER_FORMAT,
        return_state=True,
    )

    print("workspace:", WORKSPACE)
    print("stop_reason:", result.state.stop_reason)
    print("final_result:", result.state.final_result)
    print("last_analysis:", result.state.last_analysis)
    print("last_plan:", result.state.last_plan)


if __name__ == "__main__":
    main()
