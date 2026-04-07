"""Coding-oriented preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.internal.coding_impl import CodingToolSet


class FullCodingToolSet(CodingToolSet):
    """Canonical full coding bundle with editor, shell, tasks, and web tools."""

    def __init__(
        self,
        workspace_root: str,
        *,
        shell_timeout: int = 30,
        include_notebook: bool = True,
    ):
        super().__init__(
            workspace_root=workspace_root,
            shell_timeout=shell_timeout,
            include_notebook=include_notebook,
            enable_lsp=True,
            enable_tasks=True,
            enable_web=True,
            expose_legacy_aliases=True,
            expose_modern_names=True,
            profile="full",
        )


def coding_tools(
    workspace_root: str, shell_timeout: int = 30, include_notebook: bool = True
) -> ToolRegistry:
    """Build a registry with the standard full coding bundle."""
    return ToolRegistry().include_toolset(
        FullCodingToolSet(
            workspace_root=workspace_root,
            shell_timeout=shell_timeout,
            include_notebook=include_notebook,
        )
    )


__all__ = ["CodingToolSet", "FullCodingToolSet", "coding_tools"]
