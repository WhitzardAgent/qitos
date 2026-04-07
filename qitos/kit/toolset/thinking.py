"""Thinking preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.thinking import ThinkingToolSet


def thinking_tools() -> ToolRegistry:
    """Build a registry containing lightweight thought-recording tools."""
    return ToolRegistry().include_toolset(ThinkingToolSet())


__all__ = ["ThinkingToolSet", "thinking_tools"]
