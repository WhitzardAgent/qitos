"""Task-board preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.task import TaskToolSet


def task_tools(
    workspace_root: str, board_relpath: str = ".qitos/task_board.json"
) -> ToolRegistry:
    """Build a registry containing task-board tools."""
    return ToolRegistry().include_toolset(
        TaskToolSet(workspace_root=workspace_root, board_relpath=board_relpath)
    )


__all__ = ["TaskToolSet", "task_tools"]
