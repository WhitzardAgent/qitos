"""Minimal multimodal visual inspection agent for QitOS v0.5."""

# This full application is scheduled to move to qitos-zoo and is not part of QitOS core examples.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, Task, TaskResource
from qitos.kit import JsonDecisionParser, ScreenshotEnv
from qitos.models import OpenAICompatibleModel

from examples._support import SequenceModel, write_tiny_png


TASK_TEXT = "Inspect the current screenshot and summarize the most relevant UI evidence."
WORKSPACE = Path("./playground/visual_inspect_agent")
SCREENSHOT_FILE = "screen.png"
MODEL_NAME = os.getenv("QITOS_MODEL", "gpt-4.1-mini")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MAX_STEPS = 3


@dataclass
class VisualInspectState(StateSchema):
    screenshot_path: str = SCREENSHOT_FILE
    notes: list[str] = field(default_factory=list)


class VisualInspectAgent(AgentModule[VisualInspectState, dict[str, Any], Action]):
    name = "visual_inspect"

    def __init__(self, llm: Any):
        super().__init__(
            llm=llm,
            model_parser=JsonDecisionParser(),
            model_protocol="json_decision_v1",
        )

    def init_state(self, task: str, **kwargs: Any) -> VisualInspectState:
        return VisualInspectState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            screenshot_path=str(kwargs.get("screenshot_path", SCREENSHOT_FILE)),
        )

    def base_persona_prompt(self, state: VisualInspectState) -> str:
        _ = state
        return (
            "You are a visual inspection agent.\n\n"
            "Your job is to inspect the screenshot and any lightweight DOM/accessibility hints, "
            "then return a concise grounded summary of what is visible and what should happen next."
        )

    def task_policy_prompt(self, state: VisualInspectState) -> str:
        _ = state
        return (
            "Always ground your answer in the screenshot. If the screenshot and the text hints disagree, "
            "say that explicitly. If you can finish, return final mode instead of waiting."
        )

    def prepare(self, state: VisualInspectState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Screenshot path: {state.screenshot_path}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.notes:
            lines.append("Recent notes:")
            lines.extend(state.notes[-6:])
        return "\n".join(lines)

    def decide(self, state: VisualInspectState, observation: dict[str, Any]):
        _ = observation
        if state.current_step == 0:
            return None
        return Decision.final(state.final_result or "inspection complete")

    def reduce(
        self,
        state: VisualInspectState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> VisualInspectState:
        if decision.rationale:
            state.notes.append(f"Thought: {decision.rationale}")
        if decision.final_answer:
            state.notes.append(f"Final: {decision.final_answer}")
        state.notes = state.notes[-20:]
        return state


def build_model(smoke: bool = False) -> Any:
    if smoke:
        return SequenceModel(
            [
                '{"mode":"final","rationale":"The screenshot shows a login-style screen with a clear call to action.","final_answer":"The UI appears to be a login page with a prominent button and basic page chrome."}'
            ],
            model="smoke-visual-model",
        )
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.1,
        max_tokens=1200,
    )


def build_task(screenshot_path: Path) -> Task:
    return Task(
        id="visual_inspect_task",
        objective=TASK_TEXT,
        resources=[
            TaskResource(
                kind="image",
                path=screenshot_path.name,
                required=True,
                description="Primary screenshot for visual inspection.",
                metadata={"modality": "image", "detail": "high"},
            )
        ],
    )


def build_agent(smoke: bool = False) -> VisualInspectAgent:
    return VisualInspectAgent(llm=build_model(smoke=smoke))


def main(smoke: bool = False) -> None:
    workspace = WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)
    screenshot_path = workspace / SCREENSHOT_FILE
    if smoke or not screenshot_path.exists():
        write_tiny_png(screenshot_path)

    task = build_task(screenshot_path)
    env = ScreenshotEnv(
        screenshot_path=str(screenshot_path),
        text="A compact smoke screenshot for visual inspection.",
        dom={"title": "Smoke Login"},
        accessibility_tree={"role": "window", "name": "Smoke Login"},
        ocr=[{"text": "Login"}],
    )
    agent = build_agent(smoke=smoke)
    result = agent.run(
        task=task,
        workspace=str(workspace),
        env=env,
        screenshot_path=str(screenshot_path),
        max_steps=MAX_STEPS,
        render=not smoke,
        trace=not smoke,
        return_state=True,
    )

    print("workspace:", workspace)
    print("screenshot:", screenshot_path)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
