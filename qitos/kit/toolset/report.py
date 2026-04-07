"""Reporting preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.report import ReportToolSet


def report_tools(workspace_root: str) -> ToolRegistry:
    """Build a registry containing the reporting bundle."""
    return ToolRegistry().include_toolset(ReportToolSet(workspace_root=workspace_root))


__all__ = ["ReportToolSet", "report_tools"]
