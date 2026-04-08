"""Research-first coding agent with handwritten prompt, parser, protocol, and transport."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, ToolRegistry
from qitos.kit import (
    CodingToolSet,
    JsonDecisionParser,
    ReActTextParser,
    XmlDecisionParser,
    format_action,
)
from qitos.models import OpenAICompatibleModel

TASK = "Open buggy_module.py, repair add(a, b), and prove the fix with the verification command."
WORKSPACE = Path("./playground/research_harness_agent")
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MODEL_PROTOCOL = os.getenv("QITOS_PROTOCOL", "json_decision_v1")
MAX_STEPS = 8
TARGET_FILE = "buggy_module.py"
TEST_COMMAND = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'

RESEARCH_SYSTEM_PROMPT = """You are a research harness coding agent.

Mission:
- Inspect the target file before editing.
- Apply the smallest correct patch.
- Run verification as soon as the patch is ready.
- Finish only when the verification command proves the repair.

Research constraints:
- Keep reasoning short and operational.
- Use one action per step unless the active protocol supports more.
- Prefer direct file inspection over guessing.
- If verification fails, inspect the latest observation and repair precisely.
"""


@dataclass
class ResearchHarnessState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = TARGET_FILE
    test_command: str = TEST_COMMAND


def _parser_for_protocol(protocol: str) -> Any:
    normalized = str(protocol or "json_decision_v1").strip()
    if normalized == "react_text_v1":
        return ReActTextParser()
    if normalized == "xml_decision_v1":
        return XmlDecisionParser()
    return JsonDecisionParser()


class ResearchHarnessAgent(
    AgentModule[ResearchHarnessState, dict[str, Any], Action]
):
    def __init__(self, llm: Any, workspace_root: str, protocol: str):
        registry = ToolRegistry()
        registry.include(
            CodingToolSet(
                workspace_root=workspace_root,
                include_notebook=False,
                enable_lsp=False,
                enable_tasks=False,
                enable_web=False,
                expose_modern_names=False,
            )
        )
        super().__init__(
            tool_registry=registry,
            llm=llm,
            model_parser=_parser_for_protocol(protocol),
            model_protocol=protocol,
        )

    def init_state(self, task: str, **kwargs: Any) -> ResearchHarnessState:
        return ResearchHarnessState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            target_file=str(kwargs.get("target_file", TARGET_FILE)),
            test_command=str(kwargs.get("test_command", TEST_COMMAND)),
        )

    def build_system_prompt(self, state: ResearchHarnessState) -> str | None:
        _ = state
        return self.compose_system_prompt(RESEARCH_SYSTEM_PROMPT)

    def prepare(self, state: ResearchHarnessState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Verification command: {state.test_command}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ResearchHarnessState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ResearchHarnessState:
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
            if isinstance(first, dict) and int(first.get("returncode", 1)) == 0:
                state.final_result = "Verification passed from the handwritten harness path."
        state.scratchpad = state.scratchpad[-24:]
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


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-first example with handwritten prompt, parser, protocol, and transport"
    )
    parser.add_argument(
        "--protocol",
        default=MODEL_PROTOCOL,
        choices=["react_text_v1", "json_decision_v1", "xml_decision_v1"],
    )
    parser.add_argument("--workspace", default=str(WORKSPACE))
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_arg_parser().parse_args(argv)
    workspace = Path(str(args.workspace)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / TARGET_FILE
    if not target.exists():
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    agent = ResearchHarnessAgent(
        llm=build_model(),
        workspace_root=str(workspace),
        protocol=str(args.protocol),
    )
    result = agent.run(
        task=str(args.task),
        workspace=str(workspace),
        max_steps=int(args.max_steps),
        target_file=TARGET_FILE,
        test_command=TEST_COMMAND,
        return_state=True,
    )

    print("workspace:", workspace)
    print("protocol:", args.protocol)
    print("parser:", agent.model_parser.__class__.__name__)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
