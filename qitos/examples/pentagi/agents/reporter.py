"""ReporterAgent — produces final penetration test report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.reporter_prompt import REPORTER_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class ReporterState(StateSchema):
    completed_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    planned_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    report: str = ""
    max_report_chars: int = 5000
    language: str = "en"


class ReporterAgent(AgentModule[ReporterState, Any, Any]):
    """Task execution evaluator and reporter — produces final report."""

    name = "reporter"

    def __init__(self, llm: Any = None, tool_registry: Any = None,
                 language: str = "en", **config: Any):
        super().__init__(llm=llm, tool_registry=tool_registry, **config)
        self.language = language

    def prepare(self, state: ReporterState) -> str:
        """Return a concise state summary instead of full str(state) dump.

        The default str(state) includes huge completed_subtasks result data
        that causes context overflow. We only include metadata the model needs.
        """
        lines = [f"Task: {state.task}"]
        if state.completed_subtasks:
            lines.append(f"Completed subtasks: {len(state.completed_subtasks)}")
            for st in state.completed_subtasks:
                title = st.get('title', '?')
                result = str(st.get('result', ''))[:300]
                lines.append(f"  - {title}: {result}")
        if state.planned_subtasks:
            lines.append(f"Planned subtasks: {len(state.planned_subtasks)}")
            for st in state.planned_subtasks:
                lines.append(f"  - {st.get('title', '?')}")
        return "\n".join(lines)

    def init_state(self, task: str, **kwargs: Any) -> ReporterState:
        return ReporterState(
            task=task,
            max_steps=5,
            completed_subtasks=kwargs.get("completed_subtasks", []),
            planned_subtasks=kwargs.get("planned_subtasks", []),
            language=self.language,
        )

    def build_system_prompt(self, state: ReporterState) -> str | None:
        return REPORTER_SYSTEM_PROMPT.format(
            max_report_chars=state.max_report_chars,
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(self, state: ReporterState, observation: Any, decision: Decision[Any]) -> ReporterState:
        from ._reduce_utils import extract_tool_results
        tool_results = extract_tool_results(observation)
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else "")
                result = tool_results.get(tool_name, "")
                if tool_name in ("report_result", "generate_report"):
                    if isinstance(result, dict):
                        state.report = result.get("report", result.get("message", str(result)))
                    else:
                        state.report = str(result)
        # Also accept final_answer as the report (models may output free text
        # instead of a tool call when the JSON gets too long/truncated)
        if not state.report and getattr(decision, "final_answer", None):
            state.report = decision.final_answer
        # Set final_result so engine stops when report is generated
        if state.report:
            state.final_result = state.report
        return state

    def should_stop(self, state: ReporterState) -> bool:
        return bool(state.report)
