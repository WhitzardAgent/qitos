"""Alias tool: generate_report — same as ReportResultTool but with different name.

Some models (e.g., DeepSeek) may call "generate_report" instead of "report_result"
based on the system prompt. This alias ensures both names work.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolSpec


class GenerateReportTool(BaseTool):
    """Alias for ReportResultTool — accepts the same arguments but registered
    under the name "generate_report" for model compatibility.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="generate_report",
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
            "type": "generate_report",
            "message": message,
            "summary": summary,
        }


__all__ = ["GenerateReportTool"]
