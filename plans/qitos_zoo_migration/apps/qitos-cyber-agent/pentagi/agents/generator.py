"""GeneratorAgent — creates subtask list from task description (single-shot)."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.generator_prompt import GENERATOR_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class GeneratorState(StateSchema):
    generated_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    max_subtasks: int = 15
    language: str = "en"


class GeneratorAgent(AgentModule[GeneratorState, Any, Any]):
    """Optimal subtask generator — single-shot agent that creates a subtask list.

    Uses LLM to analyze the task and produce a numbered list of subtasks.
    Max 15 subtasks, each with title, description, and dependencies.
    """

    name = "generator"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 max_subtasks: int = 15, language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.max_subtasks = max_subtasks
        self.language = language

    def prepare(self, state: GeneratorState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.generated_subtasks:
            lines.append(f"Generated subtasks: {len(state.generated_subtasks)}")
            for st in state.generated_subtasks:
                lines.append(f"  - {st.get('title', '?')}: {str(st.get('description', ''))[:100]}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> GeneratorState:
        return GeneratorState(
            task=task,
            max_steps=5,  # Allow a few steps for generation
            max_subtasks=kwargs.get("max_subtasks", self.max_subtasks),
            language=self.language,
        )

    def build_system_prompt(self, state: GeneratorState) -> str | None:
        return GENERATOR_SYSTEM_PROMPT.format(
            max_subtasks=state.max_subtasks,
            docker_image=getattr(self, 'docker_image', 'kalilinux/kali-rolling'),
            current_time=datetime.datetime.now().isoformat(),
            authorized_targets=getattr(self, 'authorized_targets', []) and ", ".join(self.authorized_targets) or "all specified targets",
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(self, state: GeneratorState, observation: Any, decision: Decision[Any]) -> GeneratorState:
        from ._reduce_utils import extract_tool_results
        # Extract subtasks from observation action results.
        tool_results = extract_tool_results(observation)

        # Check for subtask_list or generate_subtasks result
        for tool_key in ("subtask_list", "generate_subtasks"):
            result = tool_results.get(tool_key, "")
            if isinstance(result, dict) and result.get("type") == "subtask_list":
                subtasks_val = result.get("subtasks")
                if isinstance(subtasks_val, str):
                    try:
                        parsed = json.loads(subtasks_val)
                        if isinstance(parsed, list):
                            state.generated_subtasks = parsed
                    except json.JSONDecodeError:
                        pass
                elif isinstance(subtasks_val, list):
                    state.generated_subtasks = subtasks_val

        # Also try to extract from decision actions (for Action objects with args)
        if not state.generated_subtasks and decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else None)
                if tool_name in ("subtask_list", "generate_subtasks"):
                    args = getattr(action, "args", None) or (action.get("args", {}) if isinstance(action, dict) else {})
                    subtasks_val = args.get("subtasks", "")
                    if isinstance(subtasks_val, str):
                        try:
                            parsed = json.loads(subtasks_val)
                            if isinstance(parsed, list):
                                state.generated_subtasks = parsed
                        except json.JSONDecodeError:
                            pass
                    elif isinstance(subtasks_val, list):
                        state.generated_subtasks = subtasks_val

        # If subtasks were generated, set final_result so the engine stops
        if state.generated_subtasks:
            state.final_result = json.dumps(state.generated_subtasks, ensure_ascii=False)
            state.set_stop("final", state.final_result)

        return state
