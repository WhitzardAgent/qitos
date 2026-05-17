"""RefinerAgent — adjusts subtasks with delta patches (single-shot)."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.refiner_prompt import REFINER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class RefinerState(StateSchema):
    completed_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    planned_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    delta_operations: List[Dict[str, Any]] = field(default_factory=list)
    max_subtasks: int = 15
    language: str = "en"


class RefinerAgent(AgentModule[RefinerState, Any, Any]):
    """Subtask plan optimizer — single-shot agent that produces delta patches.

    Analyzes completed subtask results and optimizes the remaining plan.
    Delta operations: add, remove, modify, reorder.
    """

    name = "refiner"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 max_subtasks: int = 15, language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.max_subtasks = max_subtasks
        self.language = language

    def init_state(self, task: str, **kwargs: Any) -> RefinerState:
        return RefinerState(
            task=task,
            max_steps=5,  # Allow retries
            max_subtasks=kwargs.get("max_subtasks", self.max_subtasks),
            completed_subtasks=kwargs.get("completed_subtasks", []),
            planned_subtasks=kwargs.get("planned_subtasks", []),
            language=self.language,
        )

    def prepare(self, state: RefinerState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.completed_subtasks:
            lines.append(f"Completed subtasks: {len(state.completed_subtasks)}")
            for st in state.completed_subtasks:
                title = st.get('title', '?')
                result = str(st.get('result', ''))[:200]
                lines.append(f"  - {title}: {result}")
        if state.planned_subtasks:
            lines.append(f"Planned subtasks: {len(state.planned_subtasks)}")
            for st in state.planned_subtasks:
                lines.append(f"  - {st.get('title', '?')}: {st.get('description', '')[:100]}")
        return "\n".join(lines)

    def build_system_prompt(self, state: RefinerState) -> str | None:
        return REFINER_SYSTEM_PROMPT.format(
            max_subtasks=state.max_subtasks,
            authorized_targets=getattr(self, 'authorized_targets', []) and ", ".join(self.authorized_targets) or "all specified targets",
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(self, state: RefinerState, observation: Any, decision: Decision[Any]) -> RefinerState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if tool_name in ("subtask_patch", "refine_subtasks"):
                    if isinstance(result, dict) and "operations" in result:
                        state.delta_operations = result["operations"]
                    elif isinstance(result, dict) and "delta_operations" in result:
                        delta = result["delta_operations"]
                        if isinstance(delta, str):
                            try:
                                state.delta_operations = json.loads(delta)
                            except json.JSONDecodeError:
                                pass
                        elif isinstance(delta, list):
                            state.delta_operations = delta
                    elif isinstance(result, list):
                        state.delta_operations = result
                    elif isinstance(result, str):
                        try:
                            state.delta_operations = json.loads(result)
                        except json.JSONDecodeError:
                            pass
        return state
