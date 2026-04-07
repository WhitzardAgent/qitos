"""Web-oriented preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.internal.coding_impl import CodingToolSet


class WebToolSet(CodingToolSet):
    """HTTP and HTML extraction bundle for web research and scraping flows."""

    def __init__(self):
        super().__init__(
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=True,
            expose_legacy_aliases=True,
            expose_modern_names=True,
            profile="web",
            include_http_tools=True,
        )


def web_tools() -> ToolRegistry:
    """Build a registry containing the canonical web bundle."""
    return ToolRegistry().include_toolset(WebToolSet())


__all__ = ["WebToolSet", "web_tools"]
