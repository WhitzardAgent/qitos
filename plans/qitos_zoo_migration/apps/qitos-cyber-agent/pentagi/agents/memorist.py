"""MemoristAgent — long-term memory specialist."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.memorist_prompt import MEMORIST_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class MemoristState(StateSchema):
    scratchpad: List[str] = field(default_factory=list)
    memory_results: List[dict] = field(default_factory=list)
    docker_image: str = ""
    working_dir: str = "/workspace"
    language: str = "en"


class MemoristAgent(AgentModule[MemoristState, Any, Any]):
    """Long-term memory specialist for vector DB operations."""

    name = "memorist"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 docker_image: str = "kalilinux/kali-rolling",
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.docker_image = docker_image
        self.language = language

    def prepare(self, state: MemoristState) -> str:
        """Return a concise state summary instead of full str(state) dump."""
        lines = [f"Task: {state.task}"]
        if state.scratchpad:
            lines.append(f"Recent actions: {len(state.scratchpad)}")
            for entry in state.scratchpad[-3:]:
                lines.append(f"  {entry[:200]}")
        if state.memory_results:
            lines.append(f"Memory results: {len(state.memory_results)}")
        lines.append(f"Docker: {state.docker_image}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> MemoristState:
        return MemoristState(
            task=task,
            max_steps=kwargs.get("max_steps", 8),
            docker_image=self.docker_image,
            language=self.language,
        )

    def build_system_prompt(self, state: MemoristState) -> str | None:
        from ._reduce_utils import inject_execution_context
        graphiti_section = ""
        if getattr(self, '_graphiti_enabled', False):
            graphiti_section = """<tool name="graphiti_search">
<purpose>Search the knowledge graph for episodic memory and execution history</purpose>
<usage>Find what agents discovered and executed during operations</usage>
<search_types>recent_context, episode_context, successful_tools, entity_relationships</search_types>
</tool>"""
        return inject_execution_context(self, MEMORIST_SYSTEM_PROMPT.format(
            docker_image=state.docker_image,
            working_dir=state.working_dir,
            container_ports=getattr(state, 'container_ports', '') or "N/A",
            authorized_targets=getattr(state, 'authorized_targets', []) and ", ".join(state.authorized_targets) or "As specified in task",
            graphiti_section=graphiti_section,
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        ))

    def reduce(self, state: MemoristState, observation: Any, decision: Decision[Any]) -> MemoristState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if "search" in tool_name and isinstance(result, dict):
                    results = result.get("results", [])
                    if isinstance(results, list):
                        state.memory_results.extend(results)
                state.scratchpad.append(f"{tool_name}: {str(result)[:100]}")
                # Set final_result when barrier tool is called
                if tool_name == "memorist_result":
                    summary = result.get("summary", "") if isinstance(result, dict) else str(result)
                    state.final_result = summary or str(result)
                    state.set_stop("final", state.final_result)
        return state
