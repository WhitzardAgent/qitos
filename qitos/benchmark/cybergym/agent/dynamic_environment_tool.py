"""Dynamic environment tool for CyberGym Docker-mode tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult


class DynamicEnvironmentTool(BaseTool):
    """Expose per-task dynamic Docker environment metadata to the agent."""

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name="dynamic_environment",
                description=(
                    "Show dynamic Docker environment metadata for the current task, "
                    "including the case-specific image, Dockerfile source, and the "
                    "official vulnerable binary path mounted inside the container."
                ),
                parameters={},
                required=[],
                permissions=ToolPermission(filesystem_read=True),
                concurrency_safe=True,
            )
        )

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        return ToolValidationResult.ok()

    def execute(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state = None if not runtime_context else runtime_context.get("state")
        task_root = str(getattr(state, "task_root", "") or "").strip()
        if not task_root:
            return {"status": "unavailable", "reason": "task_root is unavailable"}
        meta_path = Path(task_root) / ".cybergym" / "dynamic_environment.json"
        if not meta_path.is_file():
            return {
                "status": "unavailable",
                "reason": "dynamic environment metadata file is missing",
                "metadata_path": str(meta_path),
            }
        try:
            payload = json.loads(meta_path.read_text())
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"failed to parse metadata: {exc}",
                "metadata_path": str(meta_path),
            }
        payload.setdefault("status", "available")
        payload.setdefault("metadata_path", str(meta_path))
        return payload
