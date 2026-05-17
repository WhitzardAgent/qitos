"""Practical SWE agent: dynamic planning, branch search, and verification."""

# This full application is scheduled to move to qitos-zoo and is not part of QitOS core examples.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import (
    Action,
    AgentModule,
    Decision,
    EnvSpec,
    StateSchema,
    Task,
    TaskBudget,
    TaskResource,
    ToolRegistry,
)
from qitos.kit import (
    CodingToolSet,
    DynamicTreeSearch,
    RepoEnv,
    XmlDecisionParser,
    format_action,
    render_prompt,
)
from qitos.kit.planning import parse_numbered_plan
from qitos.models import OpenAICompatibleModel

TASK_TEXT = "Patch buggy_module.py and verify with tests."
WORKSPACE = Path("./playground/swe_agent")
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 18
TARGET_FILE = "buggy_module.py"
TEST_COMMAND = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'

PLAN_PROMPT = """You are planning a software bug-fix workflow.

Task: {task}
Target file: {file}
Verification command: {test_command}

Return a numbered plan with 3-6 short steps.
The final step must run the verification command.
Output only numbered lines.
"""

EXEC_PROMPT = """You are a SWE execution agent.

Current plan step: {current_plan_step}

Rules:
- Exactly one tool call per step.
- Prefer the smallest correct edit.
- Run verification after code changes.
- Use final answer only when the issue is resolved.

Available tools:
{tool_schema}

Output must be valid XML with exactly one <decision> root.

Act mode:
<decision mode="act">
  <action name="tool_name">
    <arg name="key">value</arg>
  </action>
</decision>

Final mode:
<decision mode="final">
  <final_answer>summary + proof</final_answer>
</decision>

Wait mode:
<decision mode="wait"></decision>
"""


@dataclass
class SWEPlanState(StateSchema):
    plan_steps: list[str] = field(default_factory=list)
    cursor: int = 0
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = TARGET_FILE
    test_command: str = TEST_COMMAND
    replan_count: int = 0


class SWEDynamicPlanningAgent(AgentModule[SWEPlanState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
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
            tool_registry=registry, llm=llm, model_parser=XmlDecisionParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> SWEPlanState:
        return SWEPlanState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            target_file=str(kwargs.get("target_file", TARGET_FILE)),
            test_command=str(kwargs.get("test_command", TEST_COMMAND)),
        )

    def build_system_prompt(self, state: SWEPlanState) -> str | None:
        return render_prompt(
            EXEC_PROMPT,
            {
                "tool_schema": self.tool_registry.get_tool_descriptions(),
                "current_plan_step": self._current_step_text(state),
            },
        )

    def prepare(self, state: SWEPlanState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Verification: {state.test_command}",
            f"Plan cursor: {state.cursor}/{len(state.plan_steps)}",
            f"Current step: {self._current_step_text(state)}",
            f"Replan count: {state.replan_count}",
        ]
        if state.plan_steps:
            lines.append("Full plan:")
            for idx, item in enumerate(state.plan_steps):
                marker = "->" if idx == state.cursor else "  "
                lines.append(f"{marker} [{idx}] {item}")
        if state.scratchpad:
            lines.append("Recent execution trace:")
            lines.extend(state.scratchpad[-10:])
        return "\n".join(lines)

    def decide(self, state: SWEPlanState, observation: dict[str, Any]):
        if not state.plan_steps or state.cursor >= len(state.plan_steps):
            if not self._make_or_refresh_plan(state):
                return Decision.final("Failed to generate a valid plan.")
            return Decision.wait("plan_ready")

        step_text = self._current_step_text(state).lower()
        candidates: list[Decision[Action]] = []

        llm_decision = self._llm_step_action(state)
        if llm_decision is not None and llm_decision.mode == "act":
            llm_decision.meta = dict(llm_decision.meta or {})
            llm_decision.meta.setdefault("score", 0.92)
            llm_decision.rationale = llm_decision.rationale or "llm_step_action"
            candidates.append(llm_decision)

        if any(token in step_text for token in ["inspect", "read", "check"]):
            candidates.append(
                Decision.act(
                    [Action(name="view", args={"path": state.target_file})],
                    rationale="inspect_target_file",
                    meta={"score": 0.8},
                )
            )
        if any(token in step_text for token in ["fix", "patch", "edit", "replace"]):
            candidates.append(
                Decision.act(
                    [
                        Action(
                            name="replace_lines",
                            args={
                                "path": state.target_file,
                                "start_line": 2,
                                "end_line": 2,
                                "replacement": "    return a + b",
                            },
                        )
                    ],
                    rationale="fallback_minimal_patch",
                    meta={"score": 0.76},
                )
            )
        if any(token in step_text for token in ["test", "verify", "validation"]):
            candidates.append(
                Decision.act(
                    [Action(name="run_command", args={"command": state.test_command})],
                    rationale="run_verification",
                    meta={"score": 0.95},
                )
            )

        if not candidates:
            return None
        return Decision.branch(
            candidates=candidates, rationale=f"dynamic_plan_step_{state.cursor}"
        )

    def reduce(
        self,
        state: SWEPlanState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> SWEPlanState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")

        should_advance = False
        if action_results:
            first = action_results[0]
            state.scratchpad.append(f"Observation: {first}")
            if isinstance(first, dict):
                if first.get("status") == "success":
                    should_advance = True
                if "returncode" in first:
                    if int(first.get("returncode", 1)) == 0:
                        should_advance = True
                        state.final_result = "Verification passed. Patch looks correct."
                    else:
                        state.replan_count += 1
                        state.cursor = len(state.plan_steps)

        if should_advance and state.cursor < len(state.plan_steps):
            state.cursor += 1

        state.scratchpad = state.scratchpad[-50:]
        return state

    def _current_step_text(self, state: SWEPlanState) -> str:
        if state.cursor < 0 or state.cursor >= len(state.plan_steps):
            return "none"
        return state.plan_steps[state.cursor]

    def _make_or_refresh_plan(self, state: SWEPlanState) -> bool:
        raw = self.llm(
            [
                {"role": "system", "content": "Return a numbered plan only."},
                {
                    "role": "user",
                    "content": render_prompt(
                        PLAN_PROMPT,
                        {
                            "task": state.task,
                            "file": state.target_file,
                            "test_command": state.test_command,
                        },
                    ),
                },
            ]
        )
        plan = parse_numbered_plan(str(raw))
        if not plan:
            return False
        state.plan_steps = plan
        state.cursor = 0
        state.scratchpad.append(f"Plan: {plan}")
        return True

    def _llm_step_action(self, state: SWEPlanState) -> Decision[Action] | None:
        step_text = self._current_step_text(state)
        if step_text == "none":
            return None
        raw = self.llm(
            [
                {"role": "system", "content": self.build_system_prompt(state) or ""},
                {
                    "role": "user",
                    "content": (
                        f"Task: {state.task}\n"
                        f"Current plan step: {step_text}\n"
                        f"Target file: {state.target_file}\n"
                        f"Verification command: {state.test_command}\n"
                        "Return exactly one XML <decision> object."
                    ),
                },
            ]
        )
        try:
            return self.model_parser.parse(str(raw))
        except Exception:
            return None


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

    task_obj = Task(
        id="swe_dynamic_planning_demo",
        objective=TASK_TEXT,
        resources=[TaskResource(kind="file", path=TARGET_FILE, required=True)],
        env_spec=EnvSpec(type="repo", config={"workspace_root": str(WORKSPACE)}),
        success_criteria=["verification command returns code 0"],
        budget=TaskBudget(max_steps=MAX_STEPS),
    )

    agent = SWEDynamicPlanningAgent(llm=build_model(), workspace_root=str(WORKSPACE))
    result = agent.run(
        task=task_obj,
        workspace=str(WORKSPACE),
        target_file=TARGET_FILE,
        test_command=TEST_COMMAND,
        max_steps=MAX_STEPS,
        env=RepoEnv(workspace_root=str(WORKSPACE)),
        search=DynamicTreeSearch(top_k=2),
        return_state=True,
    )

    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)
    print("replan_count:", result.state.replan_count)
    print("patched_file:\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
