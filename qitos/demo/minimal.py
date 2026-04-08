"""Packaged minimal coding agent used by the public quickstart and release checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema
from qitos.kit import REACT_SYSTEM_PROMPT, ReActTextParser, format_action, render_prompt
from qitos.kit.toolset import coding_tools
from qitos.models import OpenAICompatibleModel

TASK = "Fix the bug in buggy_module.py and make the verification command pass."
DEFAULT_LOGDIR = Path("./runs")
DEFAULT_WORKSPACE = Path("./playground/minimal_coding_agent")
TRACE_PREFIX = "qitos_minimal_coding"
NEXT_COMMAND = "qita board --logdir runs"
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 8
TARGET_FILE = "buggy_module.py"
TEST_COMMAND = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'


@dataclass
class MinimalCodingState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = TARGET_FILE
    test_command: str = TEST_COMMAND


class MinimalCodingAgent(AgentModule[MinimalCodingState, dict[str, Any], Action]):
    """Smallest packaged coding agent that still shows the QitOS mindset."""

    name = TRACE_PREFIX

    def __init__(self, llm: Any, workspace_root: str) -> None:
        super().__init__(
            toolset=[
                coding_tools(
                    workspace_root=workspace_root,
                    shell_timeout=20,
                    include_notebook=False,
                )
            ],
            llm=llm,
            model_parser=ReActTextParser(),
        )

    def init_state(self, task: str, **kwargs: Any) -> MinimalCodingState:
        return MinimalCodingState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            target_file=str(kwargs.get("target_file", TARGET_FILE)),
            test_command=str(kwargs.get("test_command", TEST_COMMAND)),
        )

    def build_system_prompt(self, state: MinimalCodingState) -> str | None:
        _ = state
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: MinimalCodingState) -> str:
        lines = [
            "You are repairing one tiny coding task inside a local workspace.",
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
        state: MinimalCodingState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> MinimalCodingState:
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
                state.final_result = "Patch applied and verification passed."
        state.scratchpad = state.scratchpad[-30:]
        return state


def build_model(
    *,
    api_key: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
) -> OpenAICompatibleModel:
    resolved_api_key = (
        api_key or os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or ""
    ).strip()
    if not resolved_api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running qit demo minimal."
        )
    return OpenAICompatibleModel(
        model=str(model_name or MODEL_NAME),
        api_key=resolved_api_key,
        base_url=str(base_url or MODEL_BASE_URL),
        temperature=0.2,
        max_tokens=2048,
    )


def seed_workspace(workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / TARGET_FILE
    if not target.exists():
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return target


def run_minimal_demo(
    *,
    workspace: str | Path = DEFAULT_WORKSPACE,
    trace_logdir: str | Path = DEFAULT_LOGDIR,
    render: bool = False,
    api_key: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    task: str | None = TASK,
    max_steps: int = MAX_STEPS,
    llm: Any | None = None,
) -> dict[str, str]:
    """Run the packaged minimal coding agent and return a user-facing summary."""

    workspace_path = Path(workspace).expanduser().resolve()
    logdir_path = Path(trace_logdir).expanduser().resolve()
    logdir_path.mkdir(parents=True, exist_ok=True)
    seed_workspace(workspace_path)

    model = llm if llm is not None else build_model(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
    )
    agent = MinimalCodingAgent(llm=model, workspace_root=str(workspace_path))
    result = agent.run(
        task=str(task or TASK),
        workspace=str(workspace_path),
        max_steps=max_steps,
        target_file=TARGET_FILE,
        test_command=TEST_COMMAND,
        return_state=True,
        render=render,
        trace_logdir=str(logdir_path),
        trace_prefix=TRACE_PREFIX,
    )
    run_dir = _latest_run_dir(logdir_path)
    return {
        "workspace": str(workspace_path),
        "trace_logdir": str(logdir_path),
        "trace_run": str(run_dir),
        "model_name": str(getattr(model, "model", model_name or MODEL_NAME)),
        "target_file": TARGET_FILE,
        "test_command": TEST_COMMAND,
        "final_result": str(result.state.final_result),
        "stop_reason": str(result.state.stop_reason),
        "next_step": _next_step_command(logdir_path),
    }


def main(
    workspace: str | Path = DEFAULT_WORKSPACE,
    trace_logdir: str | Path = DEFAULT_LOGDIR,
    render: bool = False,
    api_key: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    task: str | None = TASK,
    max_steps: int = MAX_STEPS,
) -> int:
    """CLI-friendly entrypoint for the public minimal coding agent demo."""

    summary = run_minimal_demo(
        workspace=workspace,
        trace_logdir=trace_logdir,
        render=render,
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        task=task,
        max_steps=max_steps,
    )
    print("model_name:", summary["model_name"])
    print("workspace:", summary["workspace"])
    print("target_file:", summary["target_file"])
    print("test_command:", summary["test_command"])
    print("trace_run:", summary["trace_run"])
    print("final_result:", summary["final_result"])
    print("stop_reason:", summary["stop_reason"])
    print("next_step:", summary["next_step"])
    return 0


def _latest_run_dir(logdir: Path) -> Path:
    candidates = sorted(
        p
        for p in logdir.iterdir()
        if p.is_dir()
        and p.name.startswith(f"{TRACE_PREFIX}_")
        and (p / "manifest.json").exists()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No traced minimal coding run was created under {logdir}."
        )
    return candidates[-1]


def _next_step_command(logdir: Path) -> str:
    try:
        relative = logdir.relative_to(Path.cwd().resolve())
        if str(relative) == ".":
            return NEXT_COMMAND
        return f"qita board --logdir {relative.as_posix()}"
    except ValueError:
        return f"qita board --logdir {logdir}"
