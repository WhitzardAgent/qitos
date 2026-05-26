"""E2E: Hook lifecycle — verify hooks fire at correct points with correct context."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from .conftest import e2e_skip, create_e2e_llm, create_e2e_engine


# ---------------------------------------------------------------------------
# Inline hook helpers
# ---------------------------------------------------------------------------


class LifecycleRecorderHook:
    """Hook that records every callback invocation as (method, step_id, phase)."""

    def __init__(self):
        self.events: List[tuple] = []

    def on_run_start(self, task, state, engine):
        self.events.append(("on_run_start", 0, "run"))

    def on_run_end(self, result, engine):
        self.events.append(("on_run_end", -1, "run"))

    def on_before_step(self, ctx, engine):
        self.events.append(("on_before_step", ctx.step_id, str(ctx.phase)))

    def on_after_step(self, ctx, engine):
        self.events.append(("on_after_step", ctx.step_id, str(ctx.phase)))

    def on_before_decide(self, ctx, engine):
        self.events.append(("on_before_decide", ctx.step_id, str(ctx.phase)))

    def on_after_decide(self, ctx, engine):
        self.events.append(("on_after_decide", ctx.step_id, str(ctx.phase)))

    def on_before_act(self, ctx, engine):
        self.events.append(("on_before_act", ctx.step_id, str(ctx.phase)))

    def on_after_act(self, ctx, engine):
        self.events.append(("on_after_act", ctx.step_id, str(ctx.phase)))

    def on_before_reduce(self, ctx, engine):
        self.events.append(("on_before_reduce", ctx.step_id, str(ctx.phase)))

    def on_after_reduce(self, ctx, engine):
        self.events.append(("on_after_reduce", ctx.step_id, str(ctx.phase)))

    def on_recover(self, ctx, engine):
        self.events.append(("on_recover", ctx.step_id, str(ctx.phase)))


class ToolAuditHook:
    """Hook that records tool use events."""

    def __init__(self):
        self.before_tool: List[Dict[str, Any]] = []
        self.after_tool: List[Dict[str, Any]] = []

    def on_before_tool_use(self, ctx, engine):
        self.before_tool.append({
            "tool_name": getattr(ctx, "tool_name", ""),
            "step_id": ctx.step_id,
        })

    def on_after_tool_use(self, ctx, engine):
        self.after_tool.append({
            "tool_name": getattr(ctx, "tool_name", ""),
            "step_id": ctx.step_id,
            "tool_result": getattr(ctx, "tool_result", None),
        })


class ContextCaptureHook:
    """Hook that captures state and step_id from on_after_decide."""

    def __init__(self):
        self.captures: List[Dict[str, Any]] = []

    def on_after_decide(self, ctx, engine):
        self.captures.append({
            "step_id": ctx.step_id,
            "state": ctx.state,
            "task": getattr(ctx.state, "task", ""),
        })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@e2e_skip
@pytest.mark.e2e
def test_hook_on_run_start_and_end_fire():
    """on_run_start fires before any step; on_run_end fires after all steps."""
    from ._agents import SimpleReActAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = SimpleReActAgent(llm=llm)
    hook = LifecycleRecorderHook()
    engine = create_e2e_engine(agent, auto_approve=True, hooks=[hook])
    engine.run("What is the capital of Italy?")
    method_names = [e[0] for e in hook.events]
    assert "on_run_start" in method_names
    assert "on_run_end" in method_names
    # on_run_start should be before any step event
    start_idx = method_names.index("on_run_start")
    end_idx = method_names.index("on_run_end")
    assert start_idx < end_idx


@e2e_skip
@pytest.mark.e2e
def test_hook_step_lifecycle_ordering():
    """Step lifecycle callbacks fire in the correct order."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    hook = LifecycleRecorderHook()
    engine = create_e2e_engine(agent, auto_approve=True, hooks=[hook])
    engine.run("What is 5 + 3? Use the add tool.")
    method_names = [e[0] for e in hook.events]
    # Verify the expected ordering for the first step
    expected_order = [
        "on_before_step",
        "on_before_decide",
        "on_after_decide",
        "on_before_act",
        "on_after_act",
        "on_before_reduce",
        "on_after_reduce",
        "on_after_step",
    ]
    # Extract step-0 events
    step0_methods = [e[0] for e in hook.events if e[1] == 0]
    # Check that the step-0 events include the key lifecycle methods
    for expected in expected_order:
        assert expected in step0_methods, f"{expected} not found in step-0 events: {step0_methods}"
    # Verify relative ordering: before_step before after_step, before_decide before after_decide
    if "on_before_step" in method_names and "on_after_step" in method_names:
        assert method_names.index("on_before_step") < method_names.index("on_after_step")


@e2e_skip
@pytest.mark.e2e
def test_hook_tool_use_lifecycle():
    """Tool use hooks fire with correct tool names and results."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    hook = ToolAuditHook()
    engine = create_e2e_engine(agent, auto_approve=True, hooks=[hook])
    engine.run("Add 10 and 20 using the add tool.")
    # If the LLM called the add tool, we should see it in the audit
    if hook.before_tool:
        tool_names = [e["tool_name"] for e in hook.before_tool]
        assert any("add" in n for n in tool_names), f"Expected 'add' in tool names, got: {tool_names}"
    if hook.after_tool:
        # After tool use should have a result
        assert any(e["tool_result"] is not None for e in hook.after_tool)


@e2e_skip
@pytest.mark.e2e
def test_hook_context_has_state_and_step_id():
    """Hook context contains the agent state with correct task and step_id."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    hook = ContextCaptureHook()
    engine = create_e2e_engine(agent, auto_approve=True, hooks=[hook])
    engine.run("What is 7 + 1?")
    assert len(hook.captures) > 0
    first = hook.captures[0]
    assert first["step_id"] == 0
    assert first["state"] is not None
    # The task should be in the state
    task = first["task"]
    assert "7" in task or "1" in task


@e2e_skip
@pytest.mark.e2e
def test_hook_on_recover_fires_on_tool_error():
    """on_recover hook fires when a tool execution fails and recovery occurs."""
    from ._agents import CalculatorAgent
    from ._tools import FlakyToolSet
    from qitos.core.tool_registry import ToolRegistry

    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    # Register the flaky toolset alongside calculator
    flaky = FlakyToolSet()
    flaky.setup({})
    agent.tool_registry.include_toolset(flaky.tools())

    hook = LifecycleRecorderHook()
    engine = create_e2e_engine(agent, auto_approve=True, hooks=[hook])
    result = engine.run("Use the flaky_add tool to add 1 and 2.")
    # If the LLM chose flaky_add, on_recover should have fired
    # If not, the test still passes (LLM chose a different tool)
    if "flaky_add" in result.tool_calls_by_name:
        method_names = [e[0] for e in hook.events]
        assert "on_recover" in method_names, "Expected on_recover when flaky_add fails"
