"""Tool registry construction for CyberGymAgent."""

from __future__ import annotations

import inspect
from typing import Any

from qitos.core.tool_registry import ToolRegistry

from ..delegate_agents import (
    build_explore_delegate_agent,
    build_insight_delegate_agent,
)
from ..submit_tool import SubmitPoCTool
from ..gdb_tool import GdbDebugTool, RunPocTool
from ..tool_names import (
    EXPLORE_DELEGATE as EXPLORE_DELEGATE_TOOL_NAME,
    INSIGHT_DELEGATE as INSIGHT_DELEGATE_TOOL_NAME,
)
from ..tracking_tools import AnalyzeDescriptionTool, RecordHypothesisTool, RecordReflectionTool, RecordChainNodeTool, RecordGateTool, RecordSinkCandidateTool, RecordAttemptTool, SwitchPhaseTool, SetCrashTypeTool
from ..analysis.tools import analysis_tools


def _load_qitos_delegate_components():
    try:
        from qitos.core.agent_spec import AgentRegistry, AgentSpec, ContextStrategy
    except ImportError:
        return None
    return AgentRegistry, AgentSpec, ContextStrategy


def build_delegate_registry(*, agent_mode: Any, llm: Any):
    mode_value = getattr(agent_mode, "value", str(agent_mode))
    components = _load_qitos_delegate_components()
    if components is None:
        return None
    AgentRegistry, AgentSpec, ContextStrategy = components
    registry = AgentRegistry()
    registered_any = False
    if mode_value in {
        "delegate_explore",
        "multi_agent_alpha",
        "multi_agent_full",
    }:
        registry.register(
            AgentSpec(
                name="explore_delegate",
                description=(
                    "Analyze bounded CyberGym repo evidence and return structured JSON. "
                    "This worker cannot submit PoCs, write files, or run commands."
                ),
                agent=build_explore_delegate_agent(llm=llm),
                tool_name=EXPLORE_DELEGATE_TOOL_NAME,
                context_strategy=ContextStrategy.ISOLATED,
                max_steps_override=4,
                shared_env=False,
            )
        )
        registered_any = True
    if mode_value in {
        "delegate_insight",
        "multi_agent_alpha",
        "multi_agent_full",
    }:
        registry.register(
            AgentSpec(
                name="insight_delegate",
                description=(
                    "Interpret recent CyberGym submit feedback and return structured JSON. "
                    "This worker cannot submit PoCs, write files, or run commands."
                ),
                agent=build_insight_delegate_agent(llm=llm),
                tool_name=INSIGHT_DELEGATE_TOOL_NAME,
                context_strategy=ContextStrategy.ISOLATED,
                max_steps_override=3,
                shared_env=False,
            )
        )
        registered_any = True
    if not registered_any:
        return None
    return registry


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
    tool_registry.register(GdbDebugTool())
    tool_registry.register(RunPocTool())
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

    agent_registry = None
    if getattr(agent, "qitos_delegate_enabled", False):
        agent_registry = build_delegate_registry(agent_mode=agent.agent_mode, llm=llm)
    if agent_registry is not None:
        for delegate_tool in agent_registry.get_delegate_tools():
            tool_registry.register(delegate_tool)

    return tool_registry, coding_tools, agent_registry
