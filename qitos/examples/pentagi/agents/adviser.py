"""AdviserAgent — pre-step guidance and technical consultation."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.adviser_prompt import ADVISER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class AdviserState(StateSchema):
    advice: str = ""
    docker_image: str = ""
    working_dir: str = "/workspace"
    container_ports: str = ""
    language: str = "en"


class AdviserAgent(AgentModule[AdviserState, Any, Any]):
    """Technical solution optimization expert — provides strategic guidance."""

    name = "adviser"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 docker_image: str = "kalilinux/kali-rolling",
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.docker_image = docker_image
        self.language = language

    def prepare(self, state: AdviserState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.advice:
            lines.append(f"Advice: {state.advice[:500]}")
        lines.append(f"Docker: {state.docker_image}")
        lines.append(f"Working dir: {state.working_dir}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> AdviserState:
        return AdviserState(
            task=task,
            max_steps=5,  # Allow retries
            docker_image=self.docker_image,
            language=self.language,
        )

    def build_system_prompt(self, state: AdviserState) -> str | None:
        execution_context = getattr(self, '_execution_context', '') or ''
        return ADVISER_SYSTEM_PROMPT.format(
            docker_image=state.docker_image,
            working_dir=state.working_dir,
            container_ports=state.container_ports or "N/A",
            execution_context=execution_context,
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(self, state: AdviserState, observation: Any, decision: Decision[Any]) -> AdviserState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if tool_name in ("provide_advice", "advice"):
                    if isinstance(result, dict):
                        state.advice = result.get("advice", result.get("message", str(result)))
                    else:
                        state.advice = str(result)
        if state.advice:
            state.final_result = state.advice
        return state

    def should_stop(self, state: AdviserState) -> bool:
        return bool(state.advice)
