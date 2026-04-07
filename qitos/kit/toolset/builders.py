"""Backward-compatible accessors for preset toolset builders."""

from __future__ import annotations

from qitos.core.tool import tool
from qitos.core.tool_registry import ToolRegistry
from .advanced import advanced_coding_tools
from .codebase import codebase_tools
from .coding import coding_tools
from .editor import editor_tools
from .notebook import notebook_tools
from .report import report_tools
from .security_audit import security_audit_tools
from .task import task_tools
from .web import web_tools


def math_tools() -> ToolRegistry:
    """Build a tiny registry of arithmetic example tools."""
    registry = ToolRegistry()

    @tool(name="add")
    def add(a: int, b: int) -> int:
        """
        Return the sum of two integers.

        :param a: First integer.
        :param b: Second integer.
        """
        return a + b

    @tool(name="multiply")
    def multiply(a: int, b: int) -> int:
        """
        Return the product of two integers.

        :param a: First integer.
        :param b: Second integer.
        """
        return a * b

    registry.register(add)
    registry.register(multiply)
    return registry


__all__ = [
    "advanced_coding_tools",
    "codebase_tools",
    "coding_tools",
    "editor_tools",
    "math_tools",
    "notebook_tools",
    "report_tools",
    "security_audit_tools",
    "task_tools",
    "web_tools",
]
