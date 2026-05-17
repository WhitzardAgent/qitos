"""Tests for AsyncEngine, EventStream, and async model adapters."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from qitos import (
    AsyncEngine,
    AgentModule,
    Decision,
    Action,
    Engine,
    EngineEvent,
    EngineEventType,
    EventStream,
    StateSchema,
    ToolRegistry,
    tool,
)
from qitos.engine import RuntimeBudget
from qitos.engine.states import RuntimePhase


# --- Minimal test fixtures ---


@dataclass
class DemoState(StateSchema):
    logs: list[str] = field(default_factory=list)


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def __init__(self, answer: str = "42"):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        self._answer = answer
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=3)

    def decide(self, state: DemoState, observation: dict[str, Any]) -> Decision[Action]:
        if state.current_step == 0:
            return Decision.act(
                actions=[Action(name="add", args={"a": 1, "b": 2})],
                rationale="use tool",
            )
        return Decision.final(self._answer)

    def reduce(
        self,
        state: DemoState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> DemoState:
        return state


# --- EventStream tests ---


class TestEventStream:
    def test_emit_and_iterate(self):
        stream = EventStream()
        events = []

        async def _consume():
            async for event in stream:
                events.append(event)

        loop = asyncio.new_event_loop()
        consume_task = loop.create_task(_consume())

        stream.emit(EngineEvent(event_type=EngineEventType.RUN_START, payload={"task": "test"}))
        stream.emit(EngineEvent(event_type=EngineEventType.STEP_START, step_id=0))
        stream.close()

        loop.run_until_complete(consume_task)
        loop.close()

        assert len(events) == 2
        assert events[0].event_type == EngineEventType.RUN_START
        assert events[1].step_id == 0

    def test_to_dict(self):
        event = EngineEvent(
            event_type=EngineEventType.DECIDE,
            step_id=1,
            agent_id="coder",
            phase=RuntimePhase.DECIDE,
            payload={"mode": "act"},
        )
        d = event.to_dict()
        assert d["event_type"] == "decide"
        assert d["step_id"] == 1
        assert d["agent_id"] == "coder"
        assert d["phase"] == "DECIDE"
        assert d["payload"]["mode"] == "act"

    def test_subscribe_fanout(self):
        stream = EventStream()
        q1 = stream.subscribe()
        q2 = stream.subscribe()

        stream.emit(EngineEvent(event_type=EngineEventType.RUN_START))
        stream.close()

        # Both queues should receive the event + close sentinel
        loop = asyncio.new_event_loop()
        e1 = loop.run_until_complete(q1.get())
        e2 = loop.run_until_complete(q2.get())
        loop.close()

        assert e1.event_type == EngineEventType.RUN_START
        assert e2.event_type == EngineEventType.RUN_START

    def test_close_signal(self):
        stream = EventStream()
        events = []

        async def _consume():
            async for event in stream:
                events.append(event)

        loop = asyncio.new_event_loop()
        task = loop.create_task(_consume())
        stream.close()
        loop.run_until_complete(task)
        loop.close()
        assert events == []


# --- EngineEventType tests ---


class TestEngineEventType:
    def test_all_types(self):
        expected = {
            "step_start", "step_end", "phase_start", "phase_end",
            "decide", "act", "reduce", "critic", "check_stop",
            "handoff", "delegate", "fanout", "error",
            "run_start", "run_end", "step_stream",
        }
        actual = {t.value for t in EngineEventType}
        assert actual == expected


# --- AsyncEngine tests ---


class TestAsyncEngine:
    def test_arun_returns_result(self):
        agent = DemoAgent(answer="hello world")
        engine = AsyncEngine(agent=agent, budget=RuntimeBudget(max_steps=5))
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(engine.arun("test task"))
        loop.close()

        assert result.step_count >= 1
        assert result.state.final_result == "hello world"

    def test_arun_stream_yields_events(self):
        agent = DemoAgent(answer="stream test")
        engine = AsyncEngine(agent=agent, budget=RuntimeBudget(max_steps=5))
        events = []

        async def _run():
            async for event in engine.arun_stream("test task"):
                events.append(event)

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()

        types = [e.event_type for e in events]
        assert EngineEventType.RUN_START in types
        assert EngineEventType.RUN_END in types
        assert EngineEventType.STEP_START in types or EngineEventType.STEP_END in types

    def test_sync_run_delegates(self):
        agent = DemoAgent(answer="sync test")
        engine = AsyncEngine(agent=agent, budget=RuntimeBudget(max_steps=5))
        result = engine.run("test task")
        assert result.state.final_result == "sync test"

    def test_engine_property(self):
        agent = DemoAgent()
        engine = AsyncEngine(agent=agent, budget=RuntimeBudget(max_steps=5))
        assert isinstance(engine.engine, Engine)
        assert engine.agent is engine.engine.agent


# --- Async model tests ---


class TestAsyncOpenAICompatibleModel:
    def test_import(self):
        from qitos.models import AsyncOpenAICompatibleModel, AsyncOpenAIModel
        assert AsyncOpenAICompatibleModel is not None
        assert AsyncOpenAIModel is not None

    def test_factory_registration(self):
        from qitos.models import ModelFactory
        assert "async-openai-compatible" in ModelFactory._providers
        assert "async-openai" in ModelFactory._providers

    def test_async_model_base(self):
        from qitos.models import AsyncModel

        class _TestAsyncModel(AsyncModel):
            async def _acall_api(self, messages):
                return "async response"

        model = _TestAsyncModel(model="test")
        # Sync call should work via asyncio.run fallback
        result = model([{"role": "user", "content": "test"}])
        assert result == "async response"

        # Async call
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(model.acall([{"role": "user", "content": "test"}]))
        loop.close()
        assert result == "async response"
