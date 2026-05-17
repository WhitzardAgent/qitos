"""EnricherAgent — enriches adviser input with context from memory."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.enricher_prompt import ENRICHER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class EnricherState(StateSchema):
    enrichment_data: str = ""
    language: str = "en"


class EnricherAgent(AgentModule[EnricherState, Any, Any]):
    """Context enrichment specialist — provides supplementary data to the adviser."""

    name = "enricher"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.language = language

    def prepare(self, state: EnricherState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.enrichment_data:
            lines.append(f"Enrichment data: {state.enrichment_data[:500]}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> EnricherState:
        return EnricherState(
            task=task,
            max_steps=3,  # A few searches + one output
            language=self.language,
        )

    def build_system_prompt(self, state: EnricherState) -> str | None:
        execution_context = getattr(self, '_execution_context', '') or ''
        return ENRICHER_SYSTEM_PROMPT.format(
            execution_context=execution_context,
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(self, state: EnricherState, observation: Any, decision: Decision[Any]) -> EnricherState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if tool_name in ("enricher_result", "provide_enrichment"):
                    if isinstance(result, dict):
                        state.enrichment_data = result.get("data", result.get("message", str(result)))
                    else:
                        state.enrichment_data = str(result)
        if state.enrichment_data:
            state.final_result = state.enrichment_data
        return state

    def should_stop(self, state: EnricherState) -> bool:
        return bool(state.enrichment_data)
