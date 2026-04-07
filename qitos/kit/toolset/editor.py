"""Editor-focused preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.internal.coding_impl import CodingToolSet


class EditorToolSet(CodingToolSet):
    """Editor-first tool bundle with file inspection and editing primitives."""

    def __init__(self, workspace_root: str):
        super().__init__(
            workspace_root=workspace_root,
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=False,
            expose_legacy_aliases=True,
            expose_modern_names=False,
            profile="editor",
        )


def editor_tools(workspace_root: str) -> ToolRegistry:
    """Build a registry containing only the editor-oriented bundle."""
    return ToolRegistry().include_toolset(EditorToolSet(workspace_root=workspace_root))


__all__ = ["EditorToolSet", "editor_tools"]
