"""QitOS computer-use example inspired by OSWorld's openai_cua_agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, EnvSpec, StateSchema, Task, TaskBudget
from qitos.kit import (
    ComputerUseToolSet,
    computer_use_persona_prompt,
    computer_use_task_policy,
    format_action,
)
from qitos.models import OpenAICompatibleModel

from examples._support import SequenceModel, write_tiny_png


TASK_TEXT = "Open the target desktop workflow, interact with the visible UI, and report the grounded outcome."
WORKSPACE = Path("./playground/openai_cua_agent")
SCREENSHOT_FILE = "desktop.png"
MODEL_NAME = os.getenv("QITOS_MODEL", "gpt-4.1-mini")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_PROTOCOL = os.getenv("QITOS_CUA_PROTOCOL", "desktop_actions_json_v1")
DEFAULT_OBSERVATION_MODE = os.getenv("QITOS_CUA_OBSERVATION_MODE", "screenshot_a11y")
MAX_STEPS = 6


@dataclass
class OpenAICUAState(StateSchema):
    observation_mode: str = DEFAULT_OBSERVATION_MODE
    trajectory: list[str] = field(default_factory=list)


class OpenAICUAAgent(AgentModule[OpenAICUAState, dict[str, Any], Action]):
    name = "openai_cua"

    def __init__(self, llm: Any, *, protocol: str = DEFAULT_PROTOCOL):
        super().__init__(llm=llm, toolset=[ComputerUseToolSet()], model_protocol=protocol)

    def init_state(self, task: str, **kwargs: Any) -> OpenAICUAState:
        return OpenAICUAState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            observation_mode=str(
                kwargs.get("observation_mode", DEFAULT_OBSERVATION_MODE)
            ),
        )

    def base_persona_prompt(self, state: OpenAICUAState) -> str:
        return computer_use_persona_prompt(state.observation_mode)

    def task_policy_prompt(self, state: OpenAICUAState) -> str:
        return computer_use_task_policy(state.observation_mode)

    def extra_instructions_prompt(self, state: OpenAICUAState) -> str:
        _ = state
        return (
            "Trajectory discipline:\n"
            "- Reflect on the most recent desktop trajectory before choosing the next action.\n"
            "- Prefer one grounded desktop action at a time.\n"
            "- Use `wait` when the UI is still changing.\n"
            "- Finish with final mode when the objective is done."
        )

    def prepare(self, state: OpenAICUAState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Observation mode: {state.observation_mode}",
            f"Protocol: {getattr(self.active_protocol(), 'id', self.active_protocol())}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.trajectory:
            lines.append("Recent trajectory:")
            lines.extend(state.trajectory[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: OpenAICUAState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> OpenAICUAState:
        if decision.rationale:
            state.trajectory.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.trajectory.append(f"Action: {format_action(decision.actions[0])}")
        action_results = observation.get("action_results", []) if isinstance(observation, dict) else []
        if action_results:
            state.trajectory.append(f"Observation: {action_results[0]}")
        state.trajectory = state.trajectory[-40:]
        return state


def build_model(smoke: bool = False) -> Any:
    if smoke:
        return SequenceModel(
            [
                '{"thought":"The screenshot shows a centered primary button, so clicking the obvious CTA is the most grounded next move.","plan":"Click the visible primary button near the center of the window.","action":{"name":"click","args":{"x":640,"y":420}}}',
                '{"thought":"The grounded click completed the workflow objective.","final_answer":"Clicked the primary CTA and completed the desktop smoke workflow."}',
            ],
            model="smoke-openai-cua",
        )
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Set OPENAI_API_KEY or QITOS_API_KEY before running this example.")
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.1,
        max_tokens=1400,
    )


def build_task(
    screenshot_path: Path,
    *,
    smoke: bool = False,
    observation_mode: str = DEFAULT_OBSERVATION_MODE,
) -> Task:
    container = str(os.getenv("QITOS_DESKTOP_CONTAINER", "")).strip()
    provider = "container" if (container and not smoke) else "mock"
    metadata = {
        "lane": "computer_use",
        "observation_mode": observation_mode,
        "provider": provider,
    }
    env_config: dict[str, Any] = {
        "provider": provider,
        "screenshot_path": str(screenshot_path),
        "instruction": TASK_TEXT,
        "accessibility_tree": {
            "role": "window",
            "name": "Desktop Smoke",
            "children": [
                {"role": "button", "name": "Continue", "bounds": [540, 390, 740, 450]}
            ],
        },
        "terminal": "$ echo desktop-smoke\ndesktop-smoke\n$ ",
        "dom": {"title": "Desktop Smoke", "buttons": ["Continue"]},
        "ocr": [{"text": "Continue", "x": 610, "y": 420}],
        "ui_candidates": [
            {"label": "Continue", "role": "button", "x": 640, "y": 420}
        ],
        "screen_size": [1280, 900],
        "metadata": metadata,
    }
    if provider == "container":
        env_config["container"] = container
        env_config["workspace_root"] = os.getenv("QITOS_DESKTOP_WORKSPACE", "/workspace")

    return Task(
        id="openai_cua_task",
        objective=TASK_TEXT,
        env_spec=EnvSpec(type="desktop", config=env_config, capabilities=["gui_observer", "gui_controller"]),
        budget=TaskBudget(max_steps=MAX_STEPS),
        metadata=metadata,
    )


def build_agent(smoke: bool = False, *, protocol: str = DEFAULT_PROTOCOL) -> OpenAICUAAgent:
    return OpenAICUAAgent(llm=build_model(smoke=smoke), protocol=protocol)


def main(smoke: bool = False) -> None:
    workspace = WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)
    screenshot_path = workspace / SCREENSHOT_FILE
    if smoke or not screenshot_path.exists():
        write_tiny_png(screenshot_path)

    observation_mode = DEFAULT_OBSERVATION_MODE
    task = build_task(screenshot_path, smoke=smoke, observation_mode=observation_mode)
    agent = build_agent(smoke=smoke, protocol=DEFAULT_PROTOCOL)
    result = agent.run(
        task=task,
        workspace=str(workspace),
        observation_mode=observation_mode,
        max_steps=MAX_STEPS,
        render=not smoke,
        trace=not smoke,
        return_state=True,
    )

    print("workspace:", workspace)
    print("protocol:", DEFAULT_PROTOCOL)
    print("observation_mode:", observation_mode)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
