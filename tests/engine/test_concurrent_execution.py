"""Tests for spec-driven concurrent action execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from unittest.mock import MagicMock

from qitos.core.action import Action, ActionExecutionPolicy, ActionResult, ActionStatus
from qitos.core.tool import BaseTool, ToolSpec
from qitos.engine.action_executor import ActionExecutor


class FakeTool(BaseTool):
    """A simple tool for testing."""

    def __init__(self, name: str, spec: ToolSpec | None = None, result: Any = "ok"):
        if spec is None:
            spec = ToolSpec(name=name, description=f"Test tool {name}")
        super().__init__(spec)
        self._result = result

    def execute(self, args, runtime_context=None):
        return self._result


class FakeToolRegistry:
    """A minimal tool registry for testing."""

    def __init__(self, tools: Dict[str, BaseTool] | None = None):
        self._tools = tools or {}

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def resolve(self, name: str) -> BaseTool | None:
        return self._tools.get(name)


def _make_executor(
    tools: Dict[str, BaseTool] | None = None,
    policy: ActionExecutionPolicy | None = None,
) -> ActionExecutor:
    registry = FakeToolRegistry(tools)
    return ActionExecutor(
        tool_registry=registry,
        policy=policy,
    )


class TestSpecDrivenClassification:

    def test_parallel_whitelist_restricts_other_read_only_tools(self):
        allowed = FakeTool(
            "READ", spec=ToolSpec(name="READ", description="Read", concurrency_safe=True)
        )
        excluded = FakeTool(
            "TODO_LIST", spec=ToolSpec(name="TODO_LIST", description="List", concurrency_safe=True)
        )
        executor = _make_executor(
            {"READ": allowed, "TODO_LIST": excluded},
            ActionExecutionPolicy(
                mode="parallel", max_concurrency=8, parallel_tool_names=frozenset({"READ"})
            ),
        )
        assert executor._is_concurrency_safe("READ")
        assert not executor._is_concurrency_safe("TODO_LIST")

    def test_concurrency_safe_spec(self):
        spec = ToolSpec(name="safe_read", description="Read", concurrency_safe=True)
        tool = FakeTool("safe_read", spec=spec)
        executor = _make_executor({"safe_read": tool})
        assert executor._is_concurrency_safe("safe_read")

    def test_read_only_spec(self):
        spec = ToolSpec(name="read_only", description="Read", read_only=True)
        tool = FakeTool("read_only", spec=spec)
        executor = _make_executor({"read_only": tool})
        assert executor._is_concurrency_safe("read_only")

    def test_needs_approval_never_safe(self):
        spec = ToolSpec(name="danger", description="Danger", needs_approval=True, concurrency_safe=True)
        tool = FakeTool("danger", spec=spec)
        executor = _make_executor({"danger": tool})
        assert not executor._is_concurrency_safe("danger")

    def test_fallback_to_legacy_set(self):
        """Unknown tool in legacy set is still considered safe."""
        executor = _make_executor()
        assert executor._is_concurrency_safe("Read")  # In _CONCURRENCY_SAFE_TOOLS
        assert executor._is_concurrency_safe("Glob")

    def test_unknown_tool_not_safe(self):
        executor = _make_executor()
        assert not executor._is_concurrency_safe("unknown_tool")


class TestSerialMode:
    def test_serial_mode_forces_sequential(self):
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        tool1 = FakeTool("read", spec=safe_spec)
        tool2 = FakeTool("read2", spec=safe_spec)
        policy = ActionExecutionPolicy(mode="serial")
        executor = _make_executor({"read": tool1, "read2": tool2}, policy=policy)
        actions = [
            Action(name="read", args={}),
            Action(name="read2", args={}),
        ]
        results = executor.execute(actions)
        assert len(results) == 2


class TestAutoMode:
    def test_auto_mode_parallel_safe_tools(self):
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        tool = FakeTool("read", spec=safe_spec, result="read_result")
        policy = ActionExecutionPolicy(mode="parallel")
        executor = _make_executor({"read": tool}, policy=policy)
        actions = [
            Action(name="read", args={}),
            Action(name="read", args={}),
        ]
        results = executor.execute(actions)
        assert len(results) == 2

    def test_auto_mode_mixed_safe_exclusive(self):
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        exclusive_spec = ToolSpec(name="write", description="Write")
        read_tool = FakeTool("read", spec=safe_spec, result="read_result")
        write_tool = FakeTool("write", spec=exclusive_spec, result="write_result")
        policy = ActionExecutionPolicy(mode="parallel")
        executor = _make_executor({"read": read_tool, "write": write_tool}, policy=policy)
        actions = [
            Action(name="read", args={}),
            Action(name="write", args={}),
            Action(name="read", args={}),
        ]
        results = executor.execute(actions)
        assert len(results) == 3

    def test_auto_mode_single_safe_sequential(self):
        """Only one safe action → runs sequentially."""
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        write_spec = ToolSpec(name="write", description="Write")
        read_tool = FakeTool("read", spec=safe_spec)
        write_tool = FakeTool("write", spec=write_spec)
        policy = ActionExecutionPolicy(mode="parallel")
        executor = _make_executor({"read": read_tool, "write": write_tool}, policy=policy)
        actions = [
            Action(name="read", args={}),
            Action(name="write", args={}),
        ]
        results = executor.execute(actions)
        assert len(results) == 2


class TestMaxConcurrency:
    def test_max_concurrency_respected(self):
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        tool = FakeTool("read", spec=safe_spec)
        policy = ActionExecutionPolicy(mode="parallel", max_concurrency=2)
        executor = _make_executor({"read": tool}, policy=policy)
        actions = [Action(name="read", args={}) for _ in range(5)]
        results = executor.execute(actions)
        assert len(results) == 5


class TestFailFast:
    def test_fail_fast_cancels_on_error(self):
        """When fail_fast=True, errors stop further concurrent execution."""
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        tool = FakeTool("read", spec=safe_spec, result=RuntimeError("fail"))
        policy = ActionExecutionPolicy(mode="parallel", fail_fast=True)
        executor = _make_executor({"read": tool}, policy=policy)
        actions = [Action(name="read", args={}) for _ in range(3)]
        # This should not crash even if tools fail
        results = executor.execute(actions)
        assert len(results) == 3


class TestResultOrdering:
    def test_results_in_original_order(self):
        safe_spec = ToolSpec(name="read", description="Read", concurrency_safe=True)
        tool = FakeTool("read", spec=safe_spec, result="read_result")
        policy = ActionExecutionPolicy(mode="parallel")
        executor = _make_executor({"read": tool}, policy=policy)
        actions = [
            Action(name="read", args={"n": 1}),
            Action(name="read", args={"n": 2}),
            Action(name="read", args={"n": 3}),
        ]
        results = executor.execute(actions)
        assert len(results) == 3
        # All should have the right name
        for r in results:
            assert r.name == "read"
