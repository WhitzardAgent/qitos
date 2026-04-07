"""Notebook-oriented preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.notebook import NotebookToolSet


def notebook_tools(workspace_root: str) -> ToolRegistry:
    """Build a registry containing notebook-specific tools."""
    return ToolRegistry().register_toolset(
        NotebookToolSet(workspace_root=workspace_root)
    )


__all__ = ["NotebookToolSet", "notebook_tools"]
