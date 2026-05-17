"""Execution context builder — renders XML context from subtask state.

Provides functions to build the structured execution context that gets
injected into agent system prompts before each invocation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..prompts.execution_context import (
    FULL_EXECUTION_CONTEXT_TEMPLATE,
    SHORT_EXECUTION_CONTEXT_TEMPLATE,
    SUBTASK_FULL_TEMPLATE,
    SUBTASK_SHORT_TEMPLATE,
    CURRENT_SUBTASK_TEMPLATE,
    CURRENT_SUBTASK_SHORT_TEMPLATE,
    TASK_FULL_TEMPLATE,
    PREVIOUS_TASKS_SECTION,
    PREVIOUS_TASKS_SECTION_EMPTY,
)


def _render_subtask_full(subtask: Dict[str, Any], max_result_chars: int = 2000) -> str:
    """Render a single subtask in full format."""
    result = subtask.get("result", "")
    # Truncate long results to avoid context overflow
    if isinstance(result, str) and len(result) > max_result_chars:
        result = result[:max_result_chars] + "...[truncated]"
    result_line = f"      <result>{result}</result>" if result else ""
    return SUBTASK_FULL_TEMPLATE.format(
        id=subtask.get("id", ""),
        title=subtask.get("title", ""),
        description=subtask.get("description", ""),
        status=subtask.get("status", "planned"),
        result_line=result_line,
    )


def _render_subtask_short(subtask: Dict[str, Any]) -> str:
    """Render a single subtask in short format."""
    return SUBTASK_SHORT_TEMPLATE.format(
        id=subtask.get("id", ""),
        title=subtask.get("title", ""),
        status=subtask.get("status", "planned"),
    )


def _render_current_subtask(
    subtask: Dict[str, Any], full: bool = True
) -> str:
    """Render the current subtask section."""
    if not subtask:
        return ""
    if full:
        return CURRENT_SUBTASK_TEMPLATE.format(
            id=subtask.get("id", ""),
            title=subtask.get("title", ""),
            description=subtask.get("description", ""),
        )
    return CURRENT_SUBTASK_SHORT_TEMPLATE.format(
        id=subtask.get("id", ""),
        title=subtask.get("title", ""),
    )


def build_execution_context(
    global_task: str,
    completed_subtasks: List[Dict[str, Any]],
    planned_subtasks: List[Dict[str, Any]],
    current_subtask: Optional[Dict[str, Any]] = None,
    previous_tasks: Optional[List[Dict[str, Any]]] = None,
    full: bool = True,
) -> str:
    """Build the execution context XML string.

    Parameters
    ----------
    global_task : str
        The overall task description.
    completed_subtasks : list[dict]
        List of completed subtask dicts.
    planned_subtasks : list[dict]
        List of planned (remaining) subtask dicts.
    current_subtask : dict | None
        The current subtask being executed (if any).
    previous_tasks : list[dict] | None
        List of previous task dicts (for multi-task flows).
    full : bool
        If True, use full template with descriptions and results.
        If False, use short template with only IDs and titles.

    Returns
    -------
    str
        Rendered XML execution context.
    """
    # Build previous tasks section
    if previous_tasks:
        tasks_xml = "\n".join(
            TASK_FULL_TEMPLATE.format(
                id=t.get("id", ""),
                title=t.get("title", ""),
                input=t.get("input", ""),
                status=t.get("status", ""),
                result=t.get("result", ""),
            )
            for t in previous_tasks
        )
        previous_tasks_section = PREVIOUS_TASKS_SECTION.format(tasks_xml=tasks_xml)
    else:
        previous_tasks_section = PREVIOUS_TASKS_SECTION_EMPTY

    if full:
        # Full context with descriptions and results
        completed_xml = "\n".join(
            _render_subtask_full(s) for s in completed_subtasks
        ) if completed_subtasks else "    (none)"

        planned_xml = "\n".join(
            _render_subtask_full(s) for s in planned_subtasks
        ) if planned_subtasks else "    (none)"

        current_section = _render_current_subtask(current_subtask or {}, full=True)

        return FULL_EXECUTION_CONTEXT_TEMPLATE.format(
            global_task=global_task,
            previous_tasks_section=previous_tasks_section,
            completed_subtasks_xml=completed_xml,
            current_subtask_section=current_section,
            planned_subtasks_xml=planned_xml,
        )
    else:
        # Short context with only IDs, titles, statuses
        completed_short = "\n".join(
            _render_subtask_short(s) for s in completed_subtasks
        ) if completed_subtasks else "    (none)"

        planned_short = "\n".join(
            _render_subtask_short(s) for s in planned_subtasks
        ) if planned_subtasks else "    (none)"

        current_section = _render_current_subtask(current_subtask or {}, full=False)

        return SHORT_EXECUTION_CONTEXT_TEMPLATE.format(
            global_task=global_task,
            previous_tasks_section=previous_tasks_section,
            completed_subtasks_short=completed_short,
            current_subtask_section_short=current_section,
            planned_subtasks_short=planned_short,
        )


__all__ = ["build_execution_context"]
