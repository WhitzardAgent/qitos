"""Advanced preset exports backed by the canonical coding toolset."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermissionDecision, ToolValidationResult
from qitos.core.tool import FunctionTool
from qitos.kit.tool.internal.coding_impl import CodingToolSet


class _DelegatingTool(BaseTool):
    """Thin BaseTool adapter that delegates all behavior to one bound method tool."""

    def __init__(self, delegate: Any):
        self._delegate = FunctionTool(delegate)
        super().__init__(deepcopy(self._delegate.spec))
        self.spec.description = str(self._delegate.spec.description)

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        return self._delegate.validate_input(args, runtime_context=runtime_context)

    def check_permissions(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionDecision:
        return self._delegate.check_permissions(args, runtime_context=runtime_context)

    def run(self, **kwargs: Any) -> Any:
        return self._delegate.run(**kwargs)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self._delegate.call(args, runtime_context=runtime_context)

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self._delegate.execute(args, runtime_context=runtime_context)


class AskUserChoiceTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).ask_user_choice)


class ToolSearchTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).tool_search)


class TodoWriteTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).todo_write)


class EnterPlanModeTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).enter_plan_mode)


class ExitPlanModeTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).exit_plan_mode)


class EnterWorktreeTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).enter_worktree)


class ExitWorktreeTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).exit_worktree)


class LSPQueryTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).lsp_query)


class MCPListResourcesTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).mcp_list_resources)


class MCPReadResourceTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).mcp_read_resource)


class CronCreateTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).cron_create)


class CronDeleteTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).cron_delete)


class CronListTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).cron_list)


class AgentSpawnTool(_DelegatingTool):
    def __init__(self):
        super().__init__(CodingToolSet(expose_legacy_aliases=False).agent_spawn)


class AdvancedCodingToolSet(CodingToolSet):
    """Claude-style coding toolset on the same canonical traditional surface."""

    name = "advanced_coding"

    def __init__(
        self,
        workspace_root: str = ".",
        *,
        enable_lsp: bool = True,
        enable_tasks: bool = True,
        enable_web: bool = True,
        include_notebook: bool = False,
    ):
        super().__init__(
            workspace_root=workspace_root,
            include_notebook=include_notebook,
            enable_lsp=enable_lsp,
            enable_tasks=enable_tasks,
            enable_web=enable_web,
            expose_legacy_aliases=True,
            expose_modern_names=False,
            profile="full",
            include_http_tools=False,
        )


__all__ = [
    "AdvancedCodingToolSet",
    "AgentSpawnTool",
    "AskUserChoiceTool",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "LSPQueryTool",
    "MCPListResourcesTool",
    "MCPReadResourceTool",
    "TodoWriteTool",
    "ToolSearchTool",
]
