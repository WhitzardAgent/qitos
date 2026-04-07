"""Native SkillHub example built on the standard QitOS agent authoring path.

This is an advanced example for the third-party skill system.
It keeps the same overall shape as the other `examples/real/` agents:

- top-level defaults
- one explicit `StateSchema`
- one `AgentModule`
- one `agent.run(...)`

Supported flows:
1. code-configured bootstrap with `skillhub:github`
2. runtime prompt-driven installation with `search_skills` / `install_skill`
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from qitos import Action, Decision, StateSchema
from qitos.kit import CodingToolSet
from qitos.kit.parser import ReActTextParser
from qitos.kit.skill import SkilledAgent
from qitos.models import OpenAIModel

TASK = (
    "Use the GitHub skill to explain how to inspect failed CI runs for a pull request."
)
WORKSPACE = "./playground/skillhub_github_agent"
MODEL_NAME = os.getenv("QITOS_MODEL", "gpt-4o-mini")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL")
MAX_STEPS = 8

SYSTEM_PROMPT = """You are a coding assistant with access to third-party skills.

When a task requires a specialized workflow:
1. Inspect currently installed skills with `list_installed_skills`
2. If needed, search with `search_skills`
3. Install the matching skill with `install_skill`
4. Follow the injected skill instructions with the normal editor and shell tools

Output format:
Thought: <reasoning>
Action: <tool_name>(arg="value")

When finished:
Final Answer: <result>
"""


@dataclass
class GitHubSkillState(StateSchema):
    scratchpad: List[str] = field(default_factory=list)


class GitHubSkillAgent(SkilledAgent[GitHubSkillState, Dict[str, Any], Action]):
    name = "skillhub_github_agent"

    def __init__(
        self,
        llm: Any,
        workspace_root: str,
        bootstrap_github_skill: bool = True,
        allow_runtime_skill_install: bool = True,
    ):
        skill_sources = ["skillhub:github"] if bootstrap_github_skill else []
        active_skills = ["github"] if bootstrap_github_skill else []

        super().__init__(
            toolset=[
                CodingToolSet(
                    workspace_root=workspace_root,
                    include_notebook=False,
                    enable_lsp=False,
                    enable_tasks=False,
                    enable_web=False,
                    expose_modern_names=False,
                )
            ],
            llm=llm,
            model_parser=ReActTextParser(),
            workspace_root=workspace_root,
            skill_sources=skill_sources,
            active_skills=active_skills,
            allow_runtime_skill_install=allow_runtime_skill_install,
        )

    def init_state(self, task: str, **kwargs: Any) -> GitHubSkillState:
        return GitHubSkillState(
            task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS))
        )

    def build_system_prompt(self, state: GitHubSkillState) -> str:
        return self.build_prompt_with_skills(
            base_prompt=SYSTEM_PROMPT,
            task=state.task,
            auto_select=True,
        )

    def prepare(self, state: GitHubSkillState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent notes:")
            lines.extend(state.scratchpad[-5:])
        return "\n".join(lines)

    def decide(
        self, state: GitHubSkillState, observation: Dict[str, Any]
    ) -> Decision[Action] | None:
        _ = state
        _ = observation
        return None

    def reduce(
        self,
        state: GitHubSkillState,
        observation: Dict[str, Any],
        decision: Decision[Action],
    ) -> GitHubSkillState:
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale[:120]}")
        for result in (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        ):
            state.scratchpad.append(str(result)[:200])
        state.scratchpad = state.scratchpad[-20:]
        return state


def build_model(args: argparse.Namespace) -> OpenAIModel:
    api_key = (
        args.api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("QITOS_API_KEY")
    )
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY or pass --api-key to run the example.")
    return OpenAIModel(
        model=args.model_name,
        api_key=api_key,
        base_url=args.model_base_url,
        temperature=args.temperature,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=WORKSPACE)
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--model-name", default=MODEL_NAME)
    parser.add_argument("--model-base-url", default=MODEL_BASE_URL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument(
        "--runtime-install",
        action="store_true",
        help="Do not preinstall github in code. Let the agent search/install it during the run.",
    )
    args = parser.parse_args()

    llm = build_model(args)
    agent = GitHubSkillAgent(
        llm=llm,
        workspace_root=args.workspace,
        bootstrap_github_skill=not args.runtime_install,
        allow_runtime_skill_install=True,
    )
    result = agent.run(
        task=args.task,
        workspace=args.workspace,
        max_steps=int(args.max_steps),
        return_state=True,
    )
    print("workspace:", args.workspace)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
