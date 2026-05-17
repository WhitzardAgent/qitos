"""Tests for Phase 1: streaming, tool-level hooks, ClaudeCodeAgent."""

import pytest
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock, patch

from qitos import AgentModule, Decision, StateSchema, Action
from qitos.models.base import Model, ModelStreamChunk, AsyncModel, ModelFactory
from qitos.engine import Engine, EngineHook, HookContext, ToolHookContext
from qitos.engine.events import EngineEvent, EngineEventType, EventStream
from qitos.engine.states import RuntimePhase


# ── ModelStreamChunk ──────────────────────────────────────────────────────────


class TestModelStreamChunk:
    def test_basic_chunk(self):
        chunk = ModelStreamChunk(text="hello", done=False)
        assert chunk.text == "hello"
        assert chunk.done is False
        assert chunk.usage is None
        assert not chunk.is_final

    def test_final_chunk(self):
        chunk = ModelStreamChunk(
            text="", done=True, usage={"prompt_tokens": 10, "completion_tokens": 5}
        )
        assert chunk.done is True
        assert chunk.is_final
        assert chunk.usage["completion_tokens"] == 5


# ── Model stream() default ────────────────────────────────────────────────────


class _DummyModel(Model):
    """A simple model that returns fixed text."""

    def _call_api(self, messages, **kwargs):
        self._set_last_usage({"prompt_tokens": 5, "completion_tokens": 3})
        return "Hello world"


class TestModelStream:
    def test_default_stream_yields_single_chunk(self):
        model = _DummyModel(model="test")
        chunks = list(model.stream([{"role": "user", "content": "hi"}]))
        assert len(chunks) == 1
        assert chunks[0].text == "Hello world"
        assert chunks[0].done is True

    def test_default_stream_sets_usage(self):
        model = _DummyModel(model="test")
        chunks = list(model.stream([{"role": "user", "content": "hi"}]))
        assert chunks[0].usage is not None
        assert chunks[0].usage["prompt_tokens"] == 5


# ── Custom streaming model ────────────────────────────────────────────────────


class _StreamingModel(Model):
    """A model that simulates token-level streaming."""

    def _call_api(self, messages, **kwargs):
        return "Hello world"

    def stream(self, messages, **kwargs):
        self._last_usage = None
        for word in ["Hello", " ", "world"]:
            yield ModelStreamChunk(text=word, done=False)
        usage = {"prompt_tokens": 3, "completion_tokens": 3}
        self._set_last_usage(usage)
        yield ModelStreamChunk(text="", done=True, usage=usage)


class TestCustomStream:
    def test_streaming_model_yields_multiple_chunks(self):
        model = _StreamingModel(model="stream-test")
        chunks = list(model.stream([{"role": "user", "content": "hi"}]))
        assert len(chunks) == 4  # 3 text chunks + 1 final
        assert chunks[0].text == "Hello"
        assert chunks[1].text == " "
        assert chunks[2].text == "world"
        assert chunks[3].done is True
        assert chunks[3].usage["completion_tokens"] == 3

    def test_streaming_model_sets_usage_on_final(self):
        model = _StreamingModel(model="stream-test")
        chunks = list(model.stream([{"role": "user", "content": "hi"}]))
        assert model._last_usage is not None
        assert model._last_usage["prompt_tokens"] == 3


# ── ToolHookContext ────────────────────────────────────────────────────────────


class TestToolHookContext:
    def test_tool_hook_context_fields(self):
        ctx = ToolHookContext(
            task="test",
            step_id=1,
            phase=RuntimePhase.ACT,
            state=None,
            tool_name="bash_v2",
            tool_args={"command": "ls"},
            tool_result="file1.py\nfile2.py",
            permission_decision="allow",
        )
        assert ctx.tool_name == "bash_v2"
        assert ctx.tool_args["command"] == "ls"
        assert ctx.tool_result == "file1.py\nfile2.py"
        assert ctx.permission_decision == "allow"

    def test_tool_hook_context_inherits_hook_context(self):
        ctx = ToolHookContext(
            task="test",
            step_id=1,
            phase=RuntimePhase.ACT,
            state=None,
        )
        assert isinstance(ctx, HookContext)
        assert ctx.task == "test"


# ── EngineHook tool-level methods ──────────────────────────────────────────────


class TestEngineHookToolMethods:
    def test_on_before_tool_use_default(self):
        hook = EngineHook()
        ctx = ToolHookContext(
            task="test", step_id=1, phase=RuntimePhase.ACT, state=None
        )
        # Should not raise
        hook.on_before_tool_use(ctx, None)

    def test_on_after_tool_use_default(self):
        hook = EngineHook()
        ctx = ToolHookContext(
            task="test", step_id=1, phase=RuntimePhase.ACT, state=None
        )
        hook.on_after_tool_use(ctx, None)

    def test_on_permission_denied_default(self):
        hook = EngineHook()
        ctx = ToolHookContext(
            task="test",
            step_id=1,
            phase=RuntimePhase.ACT,
            state=None,
            tool_name="bash_v2",
            permission_decision="deny",
        )
        hook.on_permission_denied(ctx, None)

    def test_on_before_compact_default(self):
        hook = EngineHook()
        ctx = HookContext(
            task="test", step_id=1, phase=RuntimePhase.COMPACT, state=None
        )
        hook.on_before_compact(ctx, None)

    def test_on_after_compact_default(self):
        hook = EngineHook()
        ctx = HookContext(
            task="test", step_id=1, phase=RuntimePhase.COMPACT, state=None
        )
        hook.on_after_compact(ctx, None)

    def test_on_session_start_default(self):
        hook = EngineHook()
        ctx = HookContext(
            task="test", step_id=0, phase=RuntimePhase.SESSION_START, state=None
        )
        hook.on_session_start(ctx, None)

    def test_on_session_end_default(self):
        hook = EngineHook()
        ctx = HookContext(
            task="test", step_id=0, phase=RuntimePhase.SESSION_END, state=None
        )
        hook.on_session_end(ctx, None)


# ── RuntimePhase new entries ───────────────────────────────────────────────────


class TestRuntimePhaseNewEntries:
    def test_compact_phase(self):
        assert RuntimePhase.COMPACT == "COMPACT"

    def test_session_start_phase(self):
        assert RuntimePhase.SESSION_START == "SESSION_START"

    def test_session_end_phase(self):
        assert RuntimePhase.SESSION_END == "SESSION_END"


# ── EngineEventType STEP_STREAM ────────────────────────────────────────────────


class TestStepStreamEventType:
    def test_step_stream_exists(self):
        assert EngineEventType.STEP_STREAM == "step_stream"

    def test_step_stream_event(self):
        event = EngineEvent(
            event_type=EngineEventType.STEP_STREAM,
            step_id=1,
            payload={"text": "Hello", "done": False},
        )
        assert event.event_type == EngineEventType.STEP_STREAM
        d = event.to_dict()
        assert d["event_type"] == "step_stream"
        assert d["payload"]["text"] == "Hello"


# ── ClaudeCodeAgent ────────────────────────────────────────────────────────────


class TestClaudeCodeAgent:
    def test_import(self):
        from examples.real.claude_code import ClaudeCodeAgent, ClaudeCodeState

        assert ClaudeCodeAgent is not None
        assert ClaudeCodeState is not None

    def test_state_defaults(self):
        from examples.real.claude_code.agent import ClaudeCodeState

        state = ClaudeCodeState()
        assert state.mode == "default"
        assert state.plan_mode is False

    def test_state_plan_mode(self):
        from examples.real.claude_code.agent import ClaudeCodeState

        state = ClaudeCodeState(mode="plan", plan_mode=True)
        assert state.plan_mode is True
        assert state.mode == "plan"

    def test_init_state(self):
        from examples.real.claude_code.agent import ClaudeCodeAgent, ClaudeCodeState

        model = _DummyModel(model="test")
        agent = ClaudeCodeAgent(llm=model, workspace_root=".")
        state = agent.init_state("test task")
        assert isinstance(state, ClaudeCodeState)
        assert state.mode == "default"
        assert state.plan_mode is False

    def test_init_state_plan_mode(self):
        from examples.real.claude_code.agent import ClaudeCodeAgent, ClaudeCodeState

        model = _DummyModel(model="test")
        agent = ClaudeCodeAgent(
            llm=model, workspace_root=".", permission_mode="plan"
        )
        state = agent.init_state("test task")
        assert state.plan_mode is True

    def test_build_system_prompt(self):
        from examples.real.claude_code.agent import ClaudeCodeAgent, ClaudeCodeState

        model = _DummyModel(model="test")
        agent = ClaudeCodeAgent(llm=model, workspace_root=".")
        state = ClaudeCodeState()
        prompt = agent.build_system_prompt(state)
        assert "software engineering" in prompt
        assert "Environment" in prompt  # context section from qitos.kit.context

    def test_build_system_prompt_plan_mode(self):
        from examples.real.claude_code.agent import ClaudeCodeAgent, ClaudeCodeState

        model = _DummyModel(model="test")
        agent = ClaudeCodeAgent(llm=model, workspace_root=".")
        state = ClaudeCodeState(plan_mode=True)
        prompt = agent.build_system_prompt(state)
        assert "Plan Mode" in prompt
        assert "read-only" in prompt or "Do NOT" in prompt



# ── ModelStreamChunk export ────────────────────────────────────────────────────


class TestModelStreamChunkExport:
    def test_exported_from_models(self):
        from qitos.models import ModelStreamChunk

        assert ModelStreamChunk is not None

    def test_exported_from_engine(self):
        from qitos.engine import ToolHookContext

        assert ToolHookContext is not None
