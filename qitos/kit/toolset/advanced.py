"""Advanced coding preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.advanced import AdvancedCodingToolSet


def advanced_coding_tools(
    workspace_root: str,
    *,
    enable_lsp: bool = True,
    enable_tasks: bool = True,
    enable_web: bool = True,
) -> ToolRegistry:
    """Build a Claude-style advanced coding registry."""
    return ToolRegistry().include_toolset(
        AdvancedCodingToolSet(
            workspace_root=workspace_root,
            enable_lsp=enable_lsp,
            enable_tasks=enable_tasks,
            enable_web=enable_web,
        )
    )


__all__ = ["AdvancedCodingToolSet", "advanced_coding_tools"]
