"""Barrier tools — signal agent completion or request user input.

Each agent type has its own barrier tool that signals completion
and delivers the agent's result. This matches pentagi's pattern
where each specialist has a unique barrier function.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolSpec


class BarrierDone(BaseTool):
    """Signal that the current subtask is complete.

    Used by PrimaryAgent as the generic "done" signal.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="done",
                description="Signal that the current subtask is complete. "
                "MUST be called when you have finished the assigned work. "
                "Provide a summary of what was accomplished.",
                parameters={
                    "summary": {
                        "type": "string",
                        "description": "Summary of what was accomplished",
                    },
                },
                required=["summary"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "summary": summary,
        }


class BarrierAsk(BaseTool):
    """Request user input or clarification.

    The agent calls this when it needs information from the user
    to proceed with the task.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="ask_user",
                description="Ask the user a question when you need clarification "
                "or additional information to proceed.",
                parameters={
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                },
                required=["question"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        question = str(args.get("question", ""))
        # In a real deployment, this would pause and wait for user input.
        # For now, return the question and mark as waiting.
        return {
            "status": "waiting",
            "question": question,
            "message": f"Waiting for user response to: {question}",
        }


class HackResultTool(BaseTool):
    """Pentester barrier — deliver penetration test results."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="hack_result",
                description="Deliver penetration test results. "
                "MUST be called when the pentesting task is complete. "
                "Provide a summary of findings, vulnerabilities, and results.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "Task result message summarizing findings",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the hack result",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "hack_result",
            "message": message,
            "summary": summary,
        }


class CodeResultTool(BaseTool):
    """Coder barrier — deliver code development results."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="code_result",
                description="Deliver code development results. "
                "MUST be called when the coding task is complete. "
                "Provide the code and explanation.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "Task result message describing the code",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the code result",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "code_result",
            "message": message,
            "summary": summary,
        }


class MaintenanceResultTool(BaseTool):
    """Installer barrier — deliver maintenance/installation results."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="maintenance_result",
                description="Deliver maintenance/installation results. "
                "MUST be called when the installation task is complete. "
                "Provide a summary of what was installed or configured.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "Task result message describing what was done",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the maintenance result",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "maintenance_result",
            "message": message,
            "summary": summary,
        }


class SearchResultTool(BaseTool):
    """Searcher barrier — deliver search results."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="search_result",
                description="Deliver search results. "
                "MUST be called when the search task is complete. "
                "Provide the findings and relevant information.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "Task result message with search findings",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of search results",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "search_result",
            "message": message,
            "summary": summary,
        }


class MemoristResultTool(BaseTool):
    """Memorist barrier — deliver memory operation results."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="memorist_result",
                description="Deliver memory operation results. "
                "MUST be called when the memory task is complete. "
                "Provide information about what was stored or retrieved.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "Task result message describing memory operations",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of memory result",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "memorist_result",
            "message": message,
            "summary": summary,
        }


class EnricherResultTool(BaseTool):
    """Enricher barrier — deliver enrichment data."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="enricher_result",
                description="Deliver context enrichment data. "
                "MUST be called when enrichment is complete. "
                "Provide supplementary information gathered from memory, browser, or filesystem.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "The enrichment data and supplementary context",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of enrichment findings",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "enricher_result",
            "message": message,
            "summary": summary,
        }


class SubtaskListTool(BaseTool):
    """Generator barrier — deliver generated subtask list."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="subtask_list",
                description="Deliver the generated subtask plan. "
                "MUST be called when subtask generation is complete. "
                "Provide a JSON array of subtasks with id, title, and description.",
                parameters={
                    "subtasks": {
                        "type": "string",
                        "description": "JSON array of subtasks. Each subtask must have "
                        "id (string), title (string), and description (string). "
                        'Example: [{"id":"1","title":"Recon","description":"Scan target"}]',
                    },
                    "message": {
                        "type": "string",
                        "description": "Brief message about the generated plan",
                    },
                },
                required=["subtasks", "message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        import json
        subtasks_str = str(args.get("subtasks", "[]"))
        message = str(args.get("message", ""))
        try:
            subtasks = json.loads(subtasks_str)
        except json.JSONDecodeError:
            subtasks = []
        return {
            "status": "done",
            "type": "subtask_list",
            "subtasks": subtasks,
            "message": message,
        }


class SubtaskPatchTool(BaseTool):
    """Refiner barrier — deliver subtask plan delta patches."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="subtask_patch",
                description="Deliver delta patches for the subtask plan. "
                "MUST be called when subtask refinement is complete. "
                "Provide a JSON array of delta operations (add, remove, modify, reorder).",
                parameters={
                    "deltas": {
                        "type": "string",
                        "description": "JSON array of delta operations. Each delta must have "
                        "op (string: add/remove/modify/reorder) and relevant fields. "
                        'Example: [{"op":"add","subtask":{"id":"5","title":"...","description":"..."}},'
                        '{"op":"remove","id":"3"}]',
                    },
                    "message": {
                        "type": "string",
                        "description": "Brief message about the refinement",
                    },
                },
                required=["deltas", "message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        import json
        deltas_str = str(args.get("deltas", "[]"))
        message = str(args.get("message", ""))
        try:
            deltas = json.loads(deltas_str)
        except json.JSONDecodeError:
            deltas = []
        return {
            "status": "done",
            "type": "subtask_patch",
            "deltas": deltas,
            "message": message,
        }


class ReportResultTool(BaseTool):
    """Reporter barrier — deliver final penetration test report."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="report_result",
                description="Deliver the final penetration test report. "
                "MUST be called when report generation is complete. "
                "Provide the full report content.",
                parameters={
                    "message": {
                        "type": "string",
                        "description": "The complete penetration test report",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief executive summary of the report",
                    },
                },
                required=["message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        message = str(args.get("message", ""))
        summary = str(args.get("summary", ""))
        return {
            "status": "done",
            "type": "report_result",
            "message": message,
            "summary": summary,
        }


__all__ = [
    "BarrierDone",
    "BarrierAsk",
    "HackResultTool",
    "CodeResultTool",
    "MaintenanceResultTool",
    "SearchResultTool",
    "MemoristResultTool",
    "EnricherResultTool",
    "SubtaskListTool",
    "SubtaskPatchTool",
    "ReportResultTool",
]
