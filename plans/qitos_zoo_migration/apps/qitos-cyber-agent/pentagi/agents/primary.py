"""PrimaryPentestAgent — the orchestrator for the PentAGI system."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.state import StateSchema

from ..prompts.primary_prompt import PRIMARY_SYSTEM_PROMPT
from ..prompts.shared_sections import TOOL_PLACEHOLDER


@dataclass
class PentestState(StateSchema):
    """State for the PrimaryPentestAgent."""

    current_phase: str = "generation"
    subtasks: List[Dict[str, Any]] = field(default_factory=list)
    subtask_cursor: int = 0
    current_subtask_result: str = ""
    adviser_guidance: str = ""
    scratchpad: List[str] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    completed_subtasks: List[Dict[str, Any]] = field(default_factory=list)
    report: str = ""
    authorized_targets: List[str] = field(default_factory=list)
    docker_image: str = ""
    language: str = "en"


class PrimaryPentestAgent(AgentModule[PentestState, Any, Any]):
    """Orchestrator agent for PentAGI penetration testing.

    Manages the overall flow: generation → execution → refinement → reporting.
    Delegates to specialist agents via DelegateTool instances.
    """

    name = "primary_pentest"

    def __init__(
        self,
        llm: Any = None,
        tool_registry: Any = None,
        authorized_targets: Optional[List[str]] = None,
        docker_image: str = "kalilinux/kali-rolling",
        language: str = "en",
        **config: Any,
    ):
        super().__init__(
            llm=llm,
            tool_registry=tool_registry,
            **config,
        )
        self.authorized_targets = authorized_targets or []
        self.docker_image = docker_image
        self.language = language

    def init_state(self, task: str, **kwargs: Any) -> PentestState:
        max_steps = kwargs.get("max_steps", 60)
        return PentestState(
            task=task,
            max_steps=max_steps,
            authorized_targets=self.authorized_targets,
            docker_image=self.docker_image,
            language=self.language,
        )

    def prepare(self, state: PentestState) -> str:
        """Return a concise state summary instead of full str(state) dump.

        The default str(state) includes huge completed_subtasks result data
        that causes context overflow. We only include metadata the model needs.
        """
        lines = [f"Task: {state.task}"]
        lines.append(f"Phase: {state.current_phase}")
        lines.append(f"Subtask: {state.subtask_cursor + 1}/{len(state.subtasks)}")
        if state.subtask_cursor < len(state.subtasks):
            current = state.subtasks[state.subtask_cursor]
            lines.append(f"Current subtask: {current.get('title', '?')}")
        if state.completed_subtasks:
            lines.append(f"Completed: {len(state.completed_subtasks)} subtasks")
            for st in state.completed_subtasks[-3:]:
                title = st.get('title', '?')
                result = str(st.get('result', ''))[:200]
                lines.append(f"  - {title}: {result}")
        if state.current_subtask_result:
            lines.append(f"Current result: {state.current_subtask_result[:500]}")
        if state.findings:
            lines.append(f"Findings: {len(state.findings)}")
        if state.adviser_guidance:
            lines.append(f"Adviser guidance: {state.adviser_guidance[:300]}")
        return "\n".join(lines)

    def build_system_prompt(self, state: PentestState) -> str | None:
        completed_summary = "; ".join(
            f"{s.get('title', '?')}: {s.get('result', '?')[:60]}"
            for s in state.completed_subtasks[-5:]
        )
        findings_summary = "; ".join(
            f.get("title", "?") for f in state.findings[-5:]
        )
        current_title = ""
        if state.subtask_cursor < len(state.subtasks):
            current_title = state.subtasks[state.subtask_cursor].get("title", "")

        # Use injected execution context if available, otherwise basic info
        execution_context = getattr(self, '_execution_context', '') or f"Docker: {state.docker_image}"

        # Planner section — injected by PentAGIFlow before specialist execution
        planner_section = ""
        if hasattr(self, '_planner_plan') and self._planner_plan:
            planner_section = f"<execution_plan>\n{self._planner_plan}\n</execution_plan>"

        # Ask user section — only if enabled
        ask_user_section = ""
        if hasattr(self, '_ask_user_enabled') and self._ask_user_enabled:
            ask_user_section = """\
## CUSTOMER INTERACTION PROTOCOL

<customer_communication>
- You have access to the "ask_user" tool to request additional information from the customer
- Use this tool when critical information is missing and cannot be obtained through other means
- ALL information obtained from customer interactions MUST be incorporated into your final result
</customer_communication>"""

        return PRIMARY_SYSTEM_PROMPT.format(
            authorized_targets=", ".join(state.authorized_targets) or "all specified targets",
            execution_context=execution_context,
            current_phase=state.current_phase,
            subtask_cursor=state.subtask_cursor + 1,
            total_subtasks=len(state.subtasks),
            current_subtask_title=current_title,
            completed_summary=completed_summary or "None yet",
            findings_summary=findings_summary or "None yet",
            planner_section=planner_section,
            ask_user_section=ask_user_section,
            docker_image=state.docker_image,
            current_time=datetime.datetime.now().isoformat(),
            tool_placeholder=TOOL_PLACEHOLDER,
            language=state.language,
        )

    def reduce(
        self,
        state: PentestState,
        observation: Any,
        decision: Decision[Any],
    ) -> PentestState:
        from ._reduce_utils import extract_tool_results
        # Extract tool results from observation
        tool_results = extract_tool_results(observation)

        # Process delegation results
        if decision.actions:
            for action in decision.actions:
                tool_name = getattr(action, "name", None) or (action.get("name") if isinstance(action, dict) else None)
                result = tool_results.get(tool_name, "")

                if tool_name and "delegate_to_" in tool_name:
                    if state.current_phase == "generation":
                        # Generator returned subtask list
                        if isinstance(result, dict) and "subtasks" in result:
                            state.subtasks = result["subtasks"]
                            state.current_phase = "execution"
                    elif state.current_phase == "execution":
                        # Specialist completed a subtask
                        state.current_subtask_result = str(result)
                    elif state.current_phase == "refinement":
                        # Refiner adjusted subtasks
                        if isinstance(result, dict) and "subtasks" in result:
                            state.subtasks = result["subtasks"]
                    elif state.current_phase == "reporting":
                        # Reporter produced final report
                        if isinstance(result, dict):
                            state.report = result.get("report", str(result))

                elif tool_name == "done":
                    summary = result.get("summary", "") if isinstance(result, dict) else str(result)
                    state.scratchpad.append(f"Done: {result}")
                    state.final_result = summary or str(result)
                    state.set_stop("final", state.final_result)

        return state

    def should_stop(self, state: PentestState) -> bool:
        return bool(state.report) and state.current_phase == "reporting"
