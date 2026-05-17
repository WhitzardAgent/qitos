"""CoderAgent — writes scripts and code for automation."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.coder_prompt import CODER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class CoderState(StateSchema):
    scratchpad: List[str] = field(default_factory=list)
    docker_image: str = ""
    working_dir: str = "/workspace"
    language: str = "en"


class CoderAgent(AgentModule[CoderState, Any, Any]):
    """Code development specialist for security tools and automation."""

    name = "coder"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 docker_image: str = "kalilinux/kali-rolling",
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.docker_image = docker_image
        self.language = language

    def prepare(self, state: CoderState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.scratchpad:
            lines.append(f"Recent actions: {len(state.scratchpad)}")
            for entry in state.scratchpad[-3:]:
                lines.append(f"  {entry[:200]}")
        lines.append(f"Docker: {state.docker_image}")
        lines.append(f"Working dir: {state.working_dir}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> CoderState:
        return CoderState(
            task=task,
            max_steps=kwargs.get("max_steps", 15),
            docker_image=self.docker_image,
            language=self.language,
        )

    def build_system_prompt(self, state: CoderState) -> str | None:
        from ._reduce_utils import inject_execution_context
        execution_context = getattr(self, '_execution_context', '') or ''
        return inject_execution_context(self, CODER_SYSTEM_PROMPT.format(
            docker_image=state.docker_image,
            working_dir=state.working_dir,
            container_ports=getattr(state, 'container_ports', '') or "N/A",
            authorized_targets=getattr(state, 'authorized_targets', []) and ", ".join(state.authorized_targets) or "As specified in task",
            execution_context=execution_context,
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        ))

    def reduce(self, state: CoderState, observation: Any, decision: Decision[Any]) -> CoderState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "?")
                result = tool_results.get(tool_name, "")
                state.scratchpad.append(f"{tool_name}: {str(result)[:200]}")
                # Set final_result when barrier tool is called
                if tool_name == "code_result":
                    summary = result.get("summary", "") if isinstance(result, dict) else str(result)
                    state.final_result = summary or str(result)
                    state.set_stop("final", state.final_result)
        return state
