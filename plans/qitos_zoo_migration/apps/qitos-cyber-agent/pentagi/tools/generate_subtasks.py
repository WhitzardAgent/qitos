"""Alias tool: generate_subtasks — same as SubtaskListTool but with different name.

Some models (e.g., DeepSeek) may call "generate_subtasks" instead of "subtask_list"
based on the system prompt. This alias ensures both names work.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolSpec


class GenerateSubtasksTool(BaseTool):
    """Alias for SubtaskListTool — accepts the same arguments but registered
    under the name "generate_subtasks" for model compatibility.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="generate_subtasks",
                description="Generate and deliver the subtask plan. "
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


__all__ = ["GenerateSubtasksTool"]
