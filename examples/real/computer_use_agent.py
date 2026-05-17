"""Practical computer-use agent: visit a page, extract text, and write a report."""

# This full application is scheduled to move to qitos-zoo and is not part of QitOS core examples.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, ToolRegistry
from qitos.kit import (
    CodingToolSet,
    HTMLExtractText,
    HTTPGet,
    JsonDecisionParser,
    format_action,
    render_prompt,
)
from qitos.models import OpenAICompatibleModel

TASK = "Visit the target URL, summarize the key content, and write report.md."
WORKSPACE = Path("./playground/computer_use_agent")
TARGET_URL = "https://www.thepaper.cn/newsDetail_forward_32639776"
REPORT_FILE = "report.md"
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 10

SYSTEM_PROMPT = """You are a computer-use research assistant.

Goal:
- Investigate the target page.
- Extract readable evidence.
- Produce a concise report file.

Workflow preference:
1. Fetch the page with http_get.
2. Convert HTML to readable text with extract_web_text.
3. Write report.md.
4. Optionally read the file back to verify it.
5. Finish with Final Answer.

Available tools:
{tool_schema}

Return JSON only.

Act mode:
{{
  "mode": "act",
  "rationale": "short reasoning",
  "actions": [{{"name": "tool_name", "args": {{"key": "value"}}}}]
}}

Final mode:
{{
  "mode": "final",
  "rationale": "short reasoning",
  "final_answer": "what was delivered"
}}

Wait mode:
{{
  "mode": "wait",
  "rationale": "why waiting"
}}

Constraints:
- Valid JSON only.
- Exactly one action in act mode.
- Use literal observed values in args.
"""


@dataclass
class ComputerUseState(StateSchema):
    target_url: str = TARGET_URL
    report_file: str = REPORT_FILE
    scratchpad: list[str] = field(default_factory=list)


class ComputerUseReActAgent(AgentModule[ComputerUseState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.register(HTTPGet())
        registry.register(HTMLExtractText())
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
            tool_registry=registry, llm=llm, model_parser=JsonDecisionParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> ComputerUseState:
        return ComputerUseState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            target_url=str(kwargs.get("target_url", TARGET_URL)),
            report_file=str(kwargs.get("report_file", REPORT_FILE)),
        )

    def build_system_prompt(self, state: ComputerUseState) -> str | None:
        return render_prompt(
            SYSTEM_PROMPT, {"tool_schema": self.tool_registry.get_tool_descriptions()}
        )

    def prepare(self, state: ComputerUseState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target URL: {state.target_url}",
            f"Report file: {state.report_file}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ComputerUseState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ComputerUseState:
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
    agent = ComputerUseReActAgent(llm=build_model(), workspace_root=str(WORKSPACE))
    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        target_url=TARGET_URL,
        report_file=REPORT_FILE,
        max_steps=MAX_STEPS,
        return_state=True,
    )

    report_path = WORKSPACE / REPORT_FILE
    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)
    if report_path.exists():
        print("report_file:", report_path)
        print("report_preview:\n", report_path.read_text(encoding="utf-8")[:500])


if __name__ == "__main__":
    main()
