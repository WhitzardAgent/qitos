"""Tool registry construction for CyberGymAgent."""

from __future__ import annotations

import inspect
from typing import Any

from qitos.core.tool_registry import ToolRegistry

from ...submit_tool import SubmitPoCTool
from ...tracking_tools import AnalyzeDescriptionTool, RecordHypothesisTool, RecordReflectionTool, RecordChainNodeTool, RecordGateTool, RecordSinkCandidateTool, RecordAttemptTool, SwitchPhaseTool, SetCrashTypeTool
from ...analysis.tools import analysis_tools
from .dynamic_execution import ProbeRuntimeFrontierTool, RunCandidateTool


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
        tool_registry.register(agent.READ)
        tool_registry.register(agent.GREP)
        tool_registry.register(agent.GLOB)
        tool_registry.register(agent.FindSymbols)
        tool_registry.register(agent.CallsiteSearch)
        tool_registry.register(agent.RepoMap)
        tool_registry.register(agent.FileInfo)
        tool_registry.register(agent.HexView)
        tool_registry.register(agent.StructProbe)
        tool_registry.register(agent.CorpusInspect)
        tool_registry.register(agent.WRITE)
        tool_registry.register(agent.BASH)
        tool_registry.register(coding.append_file, name=agent.APPEND_TOOL)
        tool_registry.register(coding.insert, name=agent.INSERT_TOOL)
        tool_registry.register(coding.replace_lines, name=agent.REPLACE_LINES_TOOL)
        tool_registry.register(coding.str_replace, name=agent.STR_REPLACE_TOOL)
    except ImportError:
        pass

    tool_registry.register(SubmitPoCTool(server_url=server_url))
    tool_registry.register(RunCandidateTool())
    tool_registry.register(ProbeRuntimeFrontierTool())
    tool_registry.register(RecordHypothesisTool())
    tool_registry.register(RecordReflectionTool())
    tool_registry.register(RecordChainNodeTool())
    tool_registry.register(RecordGateTool())
    tool_registry.register(RecordSinkCandidateTool())
    tool_registry.register(AnalyzeDescriptionTool())
    tool_registry.register(SetCrashTypeTool())
    tool_registry.register(RecordAttemptTool())
    tool_registry.register(SwitchPhaseTool())
    for analysis_tool in analysis_tools():
        tool_registry.register(analysis_tool)

    return tool_registry, coding_tools
