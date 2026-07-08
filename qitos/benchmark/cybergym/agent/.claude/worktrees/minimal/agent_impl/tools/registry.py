"""Tool registry construction for the Minimal CyberGym Agent."""

from __future__ import annotations

import inspect
from typing import Any

from qitos.core.tool_registry import ToolRegistry

from ...submit_tool import SubmitPoCTool
from ...tracking_tools import SinkTool, GateTool
from .dynamic_execution import GdbDebugTool


def build_tool_registry(agent: Any, *, llm: Any, shell_timeout: int, server_url: str):
    tool_registry = ToolRegistry(auto_short_aliases=True)
    coding_tools = None

    try:
        from qitos.kit.tool.internal.coding_impl import CodingToolSet

        coding_kwargs = {
            "workspace_root": agent.workspace_root,
            "shell_timeout": shell_timeout,
            "include_notebook": False,
            "enable_lsp": False,
            "enable_tasks": False,
            "enable_web": False,
            "expose_legacy_aliases": True,
            "expose_modern_names": False,
            "profile": "full",
        }
        coding_params = inspect.signature(CodingToolSet.__init__).parameters
        if (
            "auto_approve" in coding_params
            or any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in coding_params.values()
            )
        ):
            coding_kwargs["auto_approve"] = True
        coding = CodingToolSet(**coding_kwargs)
        coding_tools = coding
        # Only register the 6 core CodingToolSet methods
        tool_registry.register(agent.READ)
        tool_registry.register(agent.GREP)
        tool_registry.register(agent.GLOB)
        tool_registry.register(agent.WRITE)
        tool_registry.register(agent.BASH)
    except ImportError:
        pass

    # Domain tools
    tool_registry.register(SubmitPoCTool(server_url=server_url))
    tool_registry.register(GdbDebugTool())
    tool_registry.register(SinkTool())
    tool_registry.register(GateTool())

    return tool_registry, coding_tools
