"""SearcherAgent — information retrieval specialist."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.searcher_prompt import SEARCHER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class SearcherState(StateSchema):
    scratchpad: List[str] = field(default_factory=list)
    search_results: List[dict] = field(default_factory=list)
    language: str = "en"


class SearcherAgent(AgentModule[SearcherState, Any, Any]):
    """Precision information retrieval specialist."""

    name = "searcher"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.language = language

    def prepare(self, state: SearcherState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.scratchpad:
            lines.append(f"Recent actions: {len(state.scratchpad)}")
            for entry in state.scratchpad[-3:]:
                lines.append(f"  {entry[:200]}")
        if state.search_results:
            lines.append(f"Search results collected: {len(state.search_results)}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> SearcherState:
        return SearcherState(
            task=task,
            max_steps=kwargs.get("max_steps", 8),
            language=self.language,
        )

    def build_system_prompt(self, state: SearcherState) -> str | None:
        from ._reduce_utils import inject_execution_context
        return inject_execution_context(self, SEARCHER_SYSTEM_PROMPT.format(
            authorized_targets=getattr(self, 'authorized_targets', []) and ", ".join(self.authorized_targets) or "all specified targets",
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        ))

    def reduce(self, state: SearcherState, observation: Any, decision: Decision[Any]) -> SearcherState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if isinstance(result, dict):
                    if "search" in tool_name:
                        results = result.get("results", [])
                        if isinstance(results, list):
                            state.search_results.extend(results)
                    state.scratchpad.append(f"{tool_name}: found {len(result.get('results', []))} results")
                else:
                    state.scratchpad.append(f"{tool_name}: {str(result)[:200]}")
                # Set final_result when barrier tool is called
                if tool_name == "search_result":
                    summary = result.get("summary", "") if isinstance(result, dict) else str(result)
                    state.final_result = summary or str(result)
                    state.set_stop("final", state.final_result)
        return state
