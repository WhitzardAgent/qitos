"""Curated practical building blocks for common QiTOS agent authoring."""

import importlib
from .agent import SecurityAuditAgent, SecurityAuditState, default_security_audit_phase_engine
from .critic import ReActSelfReflectionCritic
from .env import (
    ContainerDesktopProvider,
    DesktopEnv,
    HostEnv,
    MockDesktopProvider,
    RepoEnv,
    ScreenshotEnv,
    TextWebEnv,
    TmuxEnv,
)
from .history import (
    CompactConfig,
    CompactHistory,
    TokenBudgetSummaryHistory,
    WindowHistory,
    compact_history,
)
from .memory import MarkdownFileMemory, MemdirMemory, WindowMemory
from .parser import (
    JsonDecisionParser,
    MiniMaxToolCallParser,
    ReActTextParser,
    TerminusJsonParser,
    TerminusXmlParser,
    XmlDecisionParser,
)
from .planning import (
    DynamicTreeSearch,
    NumberedPlanBuilder,
    PhaseEngine,
    PhaseSpec,
    TransitionRule,
    format_action,
)
from .prompts import (
    COMPUTER_USE_A11Y_SYSTEM_PROMPT,
    COMPUTER_USE_SCREENSHOT_A11Y_SYSTEM_PROMPT,
    COMPUTER_USE_SCREENSHOT_SYSTEM_PROMPT,
    JSON_DECISION_SYSTEM_PROMPT,
    MINIMAX_TOOL_CALL_SYSTEM_PROMPT,
    PLAN_DRAFT_PROMPT,
    PLAN_EXEC_SYSTEM_PROMPT,
    REACT_SYSTEM_PROMPT,
    SECURITY_AUDIT_SYSTEM_PROMPT,
    SWE_AGENT_SYSTEM_PROMPT,
    TERMINUS_JSON_SYSTEM_PROMPT,
    TERMINUS_TIMEOUT_PROMPT,
    TERMINUS_XML_SYSTEM_PROMPT,
    XML_DECISION_SYSTEM_PROMPT,
    computer_use_persona_prompt,
    computer_use_task_policy,
    render_prompt,
)
from .tool import (
    CodingToolSet,
    EpubToolSet,
    HTMLExtractText,
    HTTPGet,
    ReportToolSet,
    SendTerminalKeys,
    SecurityAuditToolSet,
    TaskToolSet,
    WorkspaceAwareMixin,
    security_audit_tools,
)
from .toolset import codebase_tools, coding_tools, editor_tools, report_tools, toolset_from_tools
from .toolset import ComputerUseToolSet, computer_use_tools

_LAZY_MODULE_EXPORTS = {
    "agent",
    "critic",
    "env",
    "evaluate",
    "history",
    "memory",
    "metric",
    "parser",
    "planning",
    "prompts",
    "state",
    "tool",
    "toolset",
}


def __getattr__(name: str):
    if name in _LAZY_MODULE_EXPORTS:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "critic",
    "env",
    "evaluate",
    "history",
    "memory",
    "metric",
    "parser",
    "planning",
    "prompts",
    "state",
    "tool",
    "toolset",
    "agent",
    "ReActTextParser",
    "JsonDecisionParser",
    "MiniMaxToolCallParser",
    "XmlDecisionParser",
    "TerminusJsonParser",
    "TerminusXmlParser",
    "REACT_SYSTEM_PROMPT",
    "PLAN_DRAFT_PROMPT",
    "PLAN_EXEC_SYSTEM_PROMPT",
    "XML_DECISION_SYSTEM_PROMPT",
    "JSON_DECISION_SYSTEM_PROMPT",
    "MINIMAX_TOOL_CALL_SYSTEM_PROMPT",
    "COMPUTER_USE_SCREENSHOT_SYSTEM_PROMPT",
    "COMPUTER_USE_A11Y_SYSTEM_PROMPT",
    "COMPUTER_USE_SCREENSHOT_A11Y_SYSTEM_PROMPT",
    "SWE_AGENT_SYSTEM_PROMPT",
    "SECURITY_AUDIT_SYSTEM_PROMPT",
    "TERMINUS_JSON_SYSTEM_PROMPT",
    "TERMINUS_XML_SYSTEM_PROMPT",
    "TERMINUS_TIMEOUT_PROMPT",
    "render_prompt",
    "computer_use_persona_prompt",
    "computer_use_task_policy",
    "CodingToolSet",
    "ComputerUseToolSet",
    "SendTerminalKeys",
    "SecurityAuditToolSet",
    "SecurityAuditAgent",
    "SecurityAuditState",
    "default_security_audit_phase_engine",
    "HTTPGet",
    "HTMLExtractText",
    "ReportToolSet",
    "EpubToolSet",
    "TaskToolSet",
    "WorkspaceAwareMixin",
    "toolset_from_tools",
    "coding_tools",
    "computer_use_tools",
    "editor_tools",
    "codebase_tools",
    "report_tools",
    "security_audit_tools",
    "MarkdownFileMemory",
    "WindowMemory",
    "MemdirMemory",
    "WindowHistory",
    "TokenBudgetSummaryHistory",
    "CompactConfig",
    "CompactHistory",
    "compact_history",
    "NumberedPlanBuilder",
    "DynamicTreeSearch",
    "PhaseEngine",
    "PhaseSpec",
    "TransitionRule",
    "format_action",
    "ReActSelfReflectionCritic",
    "HostEnv",
    "DesktopEnv",
    "ContainerDesktopProvider",
    "MockDesktopProvider",
    "RepoEnv",
    "ScreenshotEnv",
    "TextWebEnv",
    "TmuxEnv",
]
