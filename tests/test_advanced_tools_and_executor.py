from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from qitos import Action, StateSchema, ToolPermissionContext, ToolPermissionRule, ToolRegistry
from qitos.core.action import ActionExecutionPolicy, ActionStatus
from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult
from qitos.engine.action_executor import ActionExecutor
from qitos.kit.tool.tools import advanced_coding_tools
from qitos.kit.tool import (
    AskUserChoiceTool,
    CodingToolSet,
    LSPQueryTool,
    MCPListResourcesTool,
    MCPReadResourceTool,
    TodoWriteTool,
    ToolSearchTool,
)
from qitos.kit.tool.file import ReadFile, ReplaceLines, StrReplace
from qitos.kit.tool.shell import RunCommand


class _EchoTool(BaseTool):
    def __init__(self, name: str = "echo_tool"):
        super().__init__(
            ToolSpec(
                name=name,
                description="demo tool",
                parameters={"value": {"type": "string"}},
                required=["value"],
                permissions=ToolPermission(),
                result_max_chars=8,
            )
        )

    def validate_input(self, args, runtime_context=None):
        _ = runtime_context
        if str(args.get("value", "")) == "bad":
            return ToolValidationResult.fail("bad input", code="bad_input")
        return ToolValidationResult.ok()

    def run(self, value: str, runtime_context=None):
        _ = runtime_context
        return {"result": value}


class _SleepReadTool(BaseTool):
    def __init__(self, name: str = "sleep_read_tool", delay: float = 0.15):
        self.delay = delay
        self.starts: list[float] = []
        self._lock = threading.Lock()
        super().__init__(
            ToolSpec(
                name=name,
                description="sleepy read-only tool",
                parameters={"value": {"type": "string"}},
                required=["value"],
                permissions=ToolPermission(filesystem_read=True),
                read_only=True,
                concurrency_safe=True,
            )
        )

    def run(self, value: str, runtime_context=None):
        _ = runtime_context
        with self._lock:
            self.starts.append(time.perf_counter())
        time.sleep(self.delay)
        return {"value": value}


class _UnsafeSleepTool(BaseTool):
    def __init__(self, name: str = "unsafe_sleep_tool", delay: float = 0.05):
        self.delay = delay
        self.starts: list[float] = []
        self._lock = threading.Lock()
        super().__init__(
            ToolSpec(
                name=name,
                description="sleepy non-concurrency-safe tool",
                parameters={"value": {"type": "string"}},
                required=["value"],
                permissions=ToolPermission(filesystem_read=True),
                read_only=True,
                concurrency_safe=False,
            )
        )

    def run(self, value: str, runtime_context=None):
        _ = runtime_context
        with self._lock:
            self.starts.append(time.perf_counter())
        time.sleep(self.delay)
        return {"value": value}


class _MissingResultTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(ToolSpec(name="MISSING", description="returns nothing"))

    def run(self, runtime_context=None):
        _ = runtime_context
        return None


@dataclass
class _ExecutorState(StateSchema):
    pass


@dataclass
class _CandidateReadyState(StateSchema):
    poc_path: str = ""
    candidate_ready_for_submit: bool = False
    workspace_root: str = ""


def test_action_executor_applies_validation_permission_and_truncation():
    registry = ToolRegistry().register(_EchoTool())
    executor = ActionExecutor(registry)
    state = _ExecutorState(task="demo")

    ok = executor.execute(
        [Action(name="echo_tool", args={"value": "1234567890"})], state=state
    )[0]
    assert ok.status == ActionStatus.SUCCESS
    assert ok.output["result"].endswith("[truncated]")

    invalid = executor.execute(
        [Action(name="echo_tool", args={"value": "bad"})], state=state
    )[0]
    assert invalid.status == ActionStatus.ERROR
    assert invalid.metadata["error_category"] == "bad_input"

    state.metadata["tool_permission_context"] = ToolPermissionContext(
        deny_rules=[
            ToolPermissionRule(effect="deny", tool_name="echo_tool", message="blocked")
        ]
    )
    denied = executor.execute(
        [Action(name="echo_tool", args={"value": "ok"})], state=state
    )[0]
    assert denied.status == ActionStatus.SKIPPED
    assert denied.output["status"] == "denied"

    state.metadata["tool_permission_context"] = ToolPermissionContext(
        ask_rules=[
            ToolPermissionRule(
                effect="ask", tool_name="echo_tool", message="need approval"
            )
        ]
    )
    ask = executor.execute(
        [Action(name="echo_tool", args={"value": "ok"})], state=state
    )[0]
    assert ask.status == ActionStatus.SKIPPED
    assert ask.output["status"] == "needs_user_input"


def test_action_executor_reports_unknown_tool_without_name_repair():
    registry = ToolRegistry().register(_EchoTool(name="GREP"))
    executor = ActionExecutor(registry)

    result = executor.execute([Action(name="Grep", args={"value": "x"})])[0]

    assert result.status == ActionStatus.ERROR
    assert result.metadata["error_category"] == "tool_not_found"
    assert result.metadata["executed"] is False
    assert "Unknown tool: `Grep`" in result.output
    assert "`GREP`" in result.output


def test_action_executor_reports_missing_result_instead_of_none():
    executor = ActionExecutor(ToolRegistry().register(_MissingResultTool()))

    result = executor.execute([Action(name="MISSING", args={})])[0]

    assert result.status == ActionStatus.ERROR
    assert result.output is not None
    assert "TOOL_RESULT_MISSING" in result.output
    assert result.metadata["error_category"] == "tool_result_missing"


def test_action_executor_does_not_embed_agent_specific_candidate_policy(tmp_path):
    (tmp_path / "poc.bin").write_bytes(b"candidate")
    registry = ToolRegistry().register(_EchoTool()).register(_EchoTool(name="submit_poc"))
    executor = ActionExecutor(registry)
    state = _CandidateReadyState(
        task="demo",
        workspace_root=str(tmp_path),
        poc_path="poc.bin",
        candidate_ready_for_submit=True,
    )

    blocked = executor.execute(
        [Action(name="echo_tool", args={"value": "ignored"})],
        state=state,
    )[0]
    allowed = executor.execute(
        [Action(name="submit_poc", args={"value": "poc.bin"})],
        state=state,
    )[0]

    assert blocked.status == ActionStatus.SUCCESS
    assert allowed.status == ActionStatus.SUCCESS


def test_action_executor_allows_regeneration_when_ready_candidate_file_missing(tmp_path):
    registry = ToolRegistry().register(_EchoTool())
    executor = ActionExecutor(registry)
    state = _CandidateReadyState(
        task="demo",
        workspace_root=str(tmp_path),
        poc_path="missing.bin",
        candidate_ready_for_submit=True,
    )

    result = executor.execute(
        [Action(name="echo_tool", args={"value": "regenerate"})],
        state=state,
    )[0]

    assert result.status == ActionStatus.SUCCESS


def test_action_executor_runs_concurrency_safe_read_only_tools_in_parallel():
    tool = _SleepReadTool()
    registry = ToolRegistry().register(tool)
    executor = ActionExecutor(
        registry,
        policy=ActionExecutionPolicy(mode="parallel", max_concurrency=4),
    )

    started = time.perf_counter()
    results = executor.execute(
        [
            Action(name="sleep_read_tool", args={"value": "a"}),
            Action(name="sleep_read_tool", args={"value": "b"}),
            Action(name="sleep_read_tool", args={"value": "c"}),
        ]
    )
    elapsed = time.perf_counter() - started

    assert [item.status for item in results] == [ActionStatus.SUCCESS] * 3
    assert elapsed < 0.35
    assert len(tool.starts) == 3
    assert max(tool.starts) - min(tool.starts) < 0.08


def test_action_executor_keeps_non_concurrency_safe_tools_serial_even_in_parallel_mode():
    tool = _UnsafeSleepTool()
    registry = ToolRegistry().register(tool)
    executor = ActionExecutor(
        registry,
        policy=ActionExecutionPolicy(mode="parallel", max_concurrency=4),
    )

    started = time.perf_counter()
    results = executor.execute(
        [
            Action(name="unsafe_sleep_tool", args={"value": "a"}),
            Action(name="unsafe_sleep_tool", args={"value": "b"}),
            Action(name="unsafe_sleep_tool", args={"value": "c"}),
        ]
    )
    elapsed = time.perf_counter() - started

    assert [item.status for item in results] == [ActionStatus.SUCCESS] * 3
    assert elapsed >= 0.14
    assert len(tool.starts) == 3
    assert tool.starts[1] - tool.starts[0] >= 0.04


def test_run_command_executes_in_workspace(tmp_path):
    tool = RunCommand(workspace_root=str(tmp_path))
    result = tool.run(command="pwd")
    assert result["status"] == "success"
    assert str(tmp_path) in result["stdout"]


def test_read_file_and_str_replace_preserve_line_endings(tmp_path):
    path = tmp_path / "demo.txt"
    path.write_bytes(b"hello\r\nworld\r\n")

    reader = ReadFile(workspace_root=str(tmp_path))
    read_out = reader.run(path="demo.txt")
    assert read_out["status"] == "success"
    assert "hello" in read_out["content"]

    editor = StrReplace(workspace_root=str(tmp_path))
    edit_out = editor.run(
        path="demo.txt",
        old_str="world",
        new_str="qitos",
    )
    assert edit_out["status"] == "success"
    assert b"\r\n" in path.read_bytes()

    lines = ReplaceLines(workspace_root=str(tmp_path))
    replaced = lines.run(
        path="demo.txt", start_line=2, end_line=2, replacement="done"
    )
    assert replaced["status"] == "success"
    assert "done" in path.read_text(encoding="utf-8")


def test_web_fetch_handles_redirect_and_text_extraction(monkeypatch):
    tool = CodingToolSet()

    def _redirect(
        url: str,
        params=None,
        headers=None,
        timeout=None,
        verify_tls=True,
        allow_redirects: bool = False,
    ):
        _ = params
        _ = headers
        _ = timeout
        _ = verify_tls
        _ = allow_redirects
        return {
            "status": "success",
            "url": "https://redirected.example.com/doc",
            "status_code": 302,
            "content": "",
            "headers": {"Location": "https://redirected.example.com/doc"},
        }

    monkeypatch.setattr(tool, "http_get", _redirect)
    redirect = tool.web_fetch(url="https://example.com/doc")
    assert redirect["redirect_url"] == "https://redirected.example.com/doc"

    def _content(
        url: str,
        params=None,
        headers=None,
        timeout=None,
        verify_tls=True,
        allow_redirects: bool = False,
    ):
        _ = url
        _ = params
        _ = headers
        _ = timeout
        _ = verify_tls
        _ = allow_redirects
        return {
            "status": "success",
            "url": "https://github.com/openai/example",
            "status_code": 200,
            "content": "<html><body><p>QitOS adds advanced coding tools.</p><p>Advanced tools include bash, file edit, and tool search.</p></body></html>",
            "headers": {},
        }

    monkeypatch.setattr(tool, "http_get", _content)
    out = tool.web_fetch(url="https://github.com/openai/example")
    assert out["status"] == "success"
    assert "tool search" in out["content"].lower()
    assert out["auth_hint"]


def test_session_tools_and_tool_search(tmp_path):
    registry = advanced_coding_tools(str(tmp_path), enable_lsp=False, enable_web=False)
    state = _ExecutorState(task="advanced")
    ctx = {"state": state, "tool_registry": registry}

    todo = TodoWriteTool().run(
        todos=[{"content": "ship", "status": "pending"}], runtime_context=ctx
    )
    assert todo["count"] == 1

    plan_enter = registry.get("enter_plan_mode").run(
        reason="decompose", runtime_context=ctx
    )
    assert plan_enter["current_mode"] == "plan"

    create = registry.get("task_create").run(
        subject="Implement", description="Do the work", runtime_context=ctx
    )
    listed = registry.get("task_list").run(runtime_context=ctx)
    assert create["status"] == "success"
    assert listed["count"] == 1

    search = ToolSearchTool().run(query="plan", runtime_context=ctx)
    assert search["count"] >= 1


def test_lsp_query_and_mcp_resource_tools():
    class _FakeLSP:
        def query(self, **kwargs):
            return {"status": "success", "kwargs": kwargs}

    lsp = LSPQueryTool()
    out = lsp.run(
        operation="definition",
        symbol="demo",
        runtime_context={"ops": {"lsp": _FakeLSP()}},
    )
    assert out["status"] == "success"
    assert out["kwargs"]["operation"] == "definition"

    resources = {
        "docs": [
            {"uri": "memo://one", "text": "alpha"},
            {"uri": "memo://two", "text": "beta"},
        ]
    }
    listed = MCPListResourcesTool().run(runtime_context={"mcp_resources": resources})
    assert "docs" in listed["resources"]

    read = MCPReadResourceTool().run(
        server="docs", uri="memo://two", runtime_context={"mcp_resources": resources}
    )
    assert read["resource"]["text"] == "beta"


def test_ask_user_choice_returns_needs_input_without_answers():
    tool = AskUserChoiceTool()
    out = tool.run(
        questions=[
            {
                "header": "Mode",
                "question": "Which mode?",
                "options": [{"label": "A"}, {"label": "B"}],
            }
        ]
    )
    assert out["status"] == "needs_user_input"
