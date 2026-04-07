"""Codebase-focused preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.internal.coding_impl import CodingToolSet


class CodebaseToolSet(CodingToolSet):
    """Search-heavy bundle for large repository inspection and focused reads."""

    def __init__(self, workspace_root: str):
        super().__init__(
            workspace_root=workspace_root,
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=False,
            expose_legacy_aliases=True,
            expose_modern_names=False,
            profile="codebase",
        )


class FilesToolSet(CodingToolSet):
    """File-oriented companion bundle for generic workspace file access."""

    def __init__(self, workspace_root: str):
        super().__init__(
            workspace_root=workspace_root,
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=False,
            expose_legacy_aliases=True,
            expose_modern_names=False,
            profile="files",
        )


def codebase_tools(workspace_root: str) -> ToolRegistry:
    """Build a registry for code search plus file inspection primitives."""
    registry = ToolRegistry()
    registry.register_toolset(
        CodebaseToolSet(workspace_root=workspace_root), namespace="codebase"
    )
    registry.register_toolset(FilesToolSet(workspace_root=workspace_root), namespace="")
    return registry


__all__ = ["CodebaseToolSet", "FilesToolSet", "codebase_tools"]
