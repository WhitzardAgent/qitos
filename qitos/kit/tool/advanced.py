"""Advanced compatibility exports backed by the canonical coding toolset."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermissionDecision, ToolValidationResult
from qitos.core.tool import FunctionTool
from qitos.kit.tool.internal.coding_impl import CodingToolSet
from qitos.kit.tool.web import HTMLExtractText, HTTPGet


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


class BashV2(_DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root, expose_legacy_aliases=False
            ).bash_v2
        )

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        result = super().validate_input(args, runtime_context=runtime_context)
        if not result.valid:
            return result
        text = str((args or {}).get("command", "")).strip()
        if not text:
            return ToolValidationResult.fail("Command cannot be empty")
        destructive_tokens = (
            "rm -rf",
            "sudo rm",
            "mkfs",
            ":(){",
            "dd if=",
            "git reset --hard",
        )
        write_tokens = (
            "rm ",
            "mv ",
            "cp ",
            "mkdir ",
            "touch ",
            "sed -i",
            "> ",
            ">> ",
            "git commit",
            "git push",
        )
        if not bool((args or {}).get("allow_destructive")) and any(
            token in text for token in destructive_tokens
        ):
            return ToolValidationResult.fail(
                "Destructive command blocked", code="destructive_command"
            )
        if bool((args or {}).get("read_only")) and any(
            token in text for token in write_tokens
        ):
            return ToolValidationResult.fail(
                "Command appears to write to the workspace in read-only mode",
                code="read_only_violation",
            )
        return ToolValidationResult.ok()


class FileReadV2(_DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root, expose_legacy_aliases=False
            ).file_read_v2
        )


class FileEditV2(_DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root, expose_legacy_aliases=False
            ).file_edit_v2
        )


class GlobV2(_DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root, expose_legacy_aliases=False
            ).glob_v2
        )


class GrepV2(_DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root, expose_legacy_aliases=False
            ).grep_v2
        )


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


class _WebFetchV2Delegate:
    def __init__(self):
        self.http_get = HTTPGet()
        self.extract_web_text = HTMLExtractText()

    def run(
        self,
        url: str,
        prompt: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = runtime_context
        response = self.http_get.run(url=url, allow_redirects=False)
        if response.get("status") == "error":
            return response
        if response.get("status_code") in {301, 302, 303, 307, 308}:
            headers = response.get("headers", {})
            redirect_url = headers.get("Location") or response.get("url")
            return {
                "status": "success",
                "redirect_url": redirect_url,
                "url": response.get("url", url),
            }
        extracted = self.extract_web_text.run(html=str(response.get("content", "")))
        text = str(extracted.get("content", ""))
        result = text
        if prompt.strip():
            keywords = [item.lower() for item in prompt.split() if item.strip()]
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            picked = [
                line
                for line in lines
                if any(token in line.lower() for token in keywords)
            ]
            if picked:
                result = "\n".join(picked[:6])
        auth_hint = ""
        if "github.com" in str(response.get("url", url)):
            auth_hint = "This host may require authentication or a raw-content URL."
        return {
            "status": "success",
            "url": response.get("url", url),
            "result": result,
            "title": extracted.get("title", ""),
            "auth_hint": auth_hint,
        }


class WebFetchV2(_DelegatingTool):
    def __init__(self):
        self._impl = _WebFetchV2Delegate()
        super().__init__(self._impl.run)


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
    """Claude-style coding toolset that hides legacy aliases by default."""

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
            expose_legacy_aliases=False,
            expose_modern_names=True,
            profile="full",
            include_http_tools=False,
        )


__all__ = [
    "AdvancedCodingToolSet",
    "AgentSpawnTool",
    "AskUserChoiceTool",
    "BashV2",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "FileEditV2",
    "FileReadV2",
    "GlobV2",
    "GrepV2",
    "LSPQueryTool",
    "MCPListResourcesTool",
    "MCPReadResourceTool",
    "TodoWriteTool",
    "ToolSearchTool",
    "WebFetchV2",
]
