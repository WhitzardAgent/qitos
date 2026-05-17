"""Curated practical building blocks for common QiTOS agent authoring."""

import importlib
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
    TaskToolSet,
    WorkspaceAwareMixin,
)
from .tool.toolset import toolset_from_tools
from .toolset.codebase import codebase_tools
from .toolset.coding import coding_tools
from .toolset.computer_use import ComputerUseToolSet, computer_use_tools
from .toolset.editor import editor_tools
from .toolset.report import report_tools
from .patterns import (
    ManagerWorkerConfig,
    build_manager_worker_system,
    PlannerExecutorConfig,
    build_planner_executor_system,
    ProposerVerifierConfig,
    build_proposer_verifier_system,
)

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
    "repl",
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
    "repl",
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
    "TERMINUS_JSON_SYSTEM_PROMPT",
    "TERMINUS_XML_SYSTEM_PROMPT",
    "TERMINUS_TIMEOUT_PROMPT",
    "render_prompt",
    "computer_use_persona_prompt",
    "computer_use_task_policy",
    "CodingToolSet",
    "ComputerUseToolSet",
    "SendTerminalKeys",
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
