"""EPUB preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.epub import EpubToolSet


def epub_tools(workspace_root: str) -> ToolRegistry:
    """Build a registry containing EPUB reading tools under flat names."""
    return ToolRegistry().register_toolset(EpubToolSet(workspace_root=workspace_root), namespace="epub")


__all__ = ["EpubToolSet", "epub_tools"]
