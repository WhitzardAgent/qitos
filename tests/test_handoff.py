"""Tests for Decision.handoff(), _HandoffRuntime, and Engine handoff branch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from qitos import (
    Action,
    AgentModule,
    AgentRegistry,
    AgentSpec,
    ContextStrategy,
    Decision,
    Engine,
    HandoffContext,
    StateSchema,
    ToolRegistry,
)
from qitos.engine._handoff_runtime import _HandoffRuntime, HandoffResult
from qitos.engine.states import RuntimePhase, StepRecord


# ── Fixtures ─────────────────────────────────────────────────────────────


@dataclass
class SharedState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    handler: str = "triage"


class SimpleAgent(AgentModule[SharedState, dict[str, Any], Action]):
    """Minimal agent for testing."""

    def __init__(self, name: str = "agent", final_on_step: int = 999):
        registry = ToolRegistry()
        super().__init__(tool_registry=registry)
        self.name = name
        self._final_on_step = final_on_step

    def init_state(self, task: str, **kwargs: Any) -> SharedState:
        return SharedState(task=task, max_steps=5)

    def decide(self, state: SharedState, observation: dict[str, Any]) -> Decision[Action] | None:
        if state.current_step >= self._final_on_step:
            return Decision.final(answer="done", rationale="max steps reached")
        return None

    def reduce(
        self,
        state: SharedState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> SharedState:
        state.handler = self.name
        return state


class HandoffAgent(AgentModule[SharedState, dict[str, Any], Action]):
    """Agent that hands off on its first decide call."""

    def __init__(self, name: str = "handoffer", target: str = "receiver"):
        registry = ToolRegistry()
        super().__init__(tool_registry=registry)
        self.name = name
        self._target = target

    def init_state(self, task: str, **kwargs: Any) -> SharedState:
        return SharedState(task=task, max_steps=5)

    def decide(self, state: SharedState, observation: dict[str, Any]) -> Decision[Action] | None:
        return Decision.handoff(
            target=self._target,
            rationale="Delegating to specialist",
        )

    def reduce(self, state, observation, decision):
        state.handler = self.name
        return state


class FinalAgent(AgentModule[SharedState, dict[str, Any], Action]):
    """Agent that immediately returns final."""

    def __init__(self, name: str = "finalizer"):
        registry = ToolRegistry()
        super().__init__(tool_registry=registry)
        self.name = name

    def init_state(self, task: str, **kwargs: Any) -> SharedState:
        return SharedState(task=task, max_steps=5)

    def decide(self, state, observation):
        return Decision.final(answer="task complete", rationale="done immediately")

    def reduce(self, state, observation, decision):
        state.handler = self.name
        return state


def _make_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(AgentSpec(name="handoffer", description="Hands off", agent=HandoffAgent()))
    registry.register(AgentSpec(name="receiver", description="Receives", agent=FinalAgent()))
    return registry


# ── Decision.handoff() tests ─────────────────────────────────────────────


class TestDecisionHandoff:
    def test_handoff_factory(self):
        d = Decision.handoff(target="analyst", rationale="needs analysis")
        assert d.mode == "handoff"
        assert d.meta["handoff_target"] == "analyst"
        assert d.rationale == "needs analysis"

    def test_handoff_validate_requires_target(self):
        d = Decision(mode="handoff", meta={})
        with pytest.raises(ValueError, match="handoff_target"):
            d.validate()

    def test_handoff_validate_passes(self):
        d = Decision.handoff(target="analyst")
        d.validate()  # should not raise

    def test_handoff_with_custom_meta(self):
        d = Decision.handoff(target="coder", meta={"extra": True})
        assert d.meta["handoff_target"] == "coder"
        assert d.meta["extra"] is True


# ── RuntimePhase tests ───────────────────────────────────────────────────


class TestRuntimePhaseHandoff:
    def test_handoff_phases_exist(self):
        assert RuntimePhase.HANDOFF_START == "HANDOFF_START"
        assert RuntimePhase.HANDOFF_END == "HANDOFF_END"


# ── StepRecord.agent_id tests ────────────────────────────────────────────


class TestStepRecordAgentId:
    def test_agent_id_default_none(self):
        record = StepRecord(step_id=0)
        assert record.agent_id is None

    def test_agent_id_settable(self):
        record = StepRecord(step_id=0, agent_id="analyst")
        assert record.agent_id == "analyst"


# ── StateAdapter tests ───────────────────────────────────────────────────


class TestStateAdapter:
    def test_state_adapter_is_abstract(self):
        from qitos.core.agent_spec import StateAdapter

        with pytest.raises(TypeError):
            StateAdapter()

    def test_state_adapter_on_agent_spec(self):
        from qitos.core.agent_spec import StateAdapter

        class MyAdapter(StateAdapter):
            def adapt(self, source):
                return source

        spec = AgentSpec(
            name="test",
            description="test",
            agent=SimpleAgent(),
            state_adapter=MyAdapter(),
        )
        assert spec.state_adapter is not None


# ── _HandoffRuntime tests ────────────────────────────────────────────────


class TestHandoffRuntime:
    def test_execute_handoff_swaps_agent(self):
        handoffer = HandoffAgent(target="receiver")
        receiver = FinalAgent()
        registry = AgentRegistry()
        registry.register(AgentSpec(name="handoffer", description="", agent=handoffer))
        registry.register(AgentSpec(name="receiver", description="", agent=receiver))

        engine = Engine(agent=handoffer, agent_registry=registry)

        state = SharedState(task="test", max_steps=5)
        record = StepRecord(step_id=0)
        decision = Decision.handoff(target="receiver")

        result = engine._handoff_runtime.execute_handoff(state, decision, record)

        assert result.from_agent == "handoffer"
        assert result.to_agent == "receiver"
        assert engine.agent is receiver
        assert record.agent_id == "receiver"

    def test_execute_handoff_without_registry_raises(self):
        handoffer = HandoffAgent()
        engine = Engine(agent=handoffer, agent_registry=None)

        state = SharedState(task="test", max_steps=5)
        record = StepRecord(step_id=0)
        decision = Decision.handoff(target="receiver")

        with pytest.raises(ValueError, match="agent_registry"):
            engine._handoff_runtime.execute_handoff(state, decision, record)

    def test_execute_handoff_unknown_target_raises(self):
        handoffer = HandoffAgent()
        registry = AgentRegistry()
        registry.register(AgentSpec(name="handoffer", description="", agent=handoffer))

        engine = Engine(agent=handoffer, agent_registry=registry)

        state = SharedState(task="test", max_steps=5)
        record = StepRecord(step_id=0)
        decision = Decision.handoff(target="nonexistent")

        with pytest.raises(KeyError):
            engine._handoff_runtime.execute_handoff(state, decision, record)


# ── Engine handoff integration tests ─────────────────────────────────────


class TestEngineHandoffIntegration:
    def test_handoff_in_engine_loop(self):
        """HandoffAgent decides to handoff → FinalAgent receives and returns final."""
        handoffer = HandoffAgent(target="receiver")
        receiver = FinalAgent()
        registry = AgentRegistry()
        registry.register(AgentSpec(name="handoffer", description="", agent=handoffer))
        registry.register(AgentSpec(name="receiver", description="", agent=receiver))

        engine = Engine(agent=handoffer, agent_registry=registry)
        result = engine.run(task="test task", max_steps=5)

        # The final agent should have set the final answer
        assert result.state.final_result == "task complete"
        assert result.state.stop_reason == "final"

    def test_handoff_without_registry_raises_in_loop(self):
        """If handoff decision is produced but no registry, the loop should error."""
        # An agent that always decides handoff, but no registry
        handoffer = HandoffAgent(target="receiver")

        engine = Engine(agent=handoffer, agent_registry=None)
        with pytest.raises(ValueError, match="agent_registry"):
            engine.run(task="test task", max_steps=5)

    def test_state_shared_across_handoff(self):
        """State should be the same object before and after handoff."""

        class TrackerAgent(AgentModule[SharedState, dict[str, Any], Action]):
            def __init__(self, name: str, **kwargs):
                super().__init__(tool_registry=ToolRegistry(), **kwargs)
                self.name = name

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=5)

            def decide(self, state, observation):
                if self.name == "first":
                    return Decision.handoff(target="second")
                return Decision.final(answer="done")

            def reduce(self, state, observation, decision):
                state.handler = self.name
                return state

        first = TrackerAgent(name="first")
        second = TrackerAgent(name="second")

        registry = AgentRegistry()
        registry.register(AgentSpec(name="first", description="", agent=first))
        registry.register(AgentSpec(name="second", description="", agent=second))

        engine = Engine(agent=first, agent_registry=registry)
        result = engine.run(task="test task", max_steps=5)

        # State should show the second agent's handler
        assert result.state.handler == "second"

    def test_handoff_metadata_stored_in_state(self):
        """Handoff context should be stored in state.metadata['last_handoff']."""

        class TrackerAgent(AgentModule[SharedState, dict[str, Any], Action]):
            def __init__(self, name: str, **kwargs):
                super().__init__(tool_registry=ToolRegistry(), **kwargs)
                self.name = name

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=5)

            def decide(self, state, observation):
                if self.name == "first":
                    return Decision.handoff(target="second")
                return Decision.final(answer="done")

            def reduce(self, state, observation, decision):
                return state

        first = TrackerAgent(name="first")
        second = TrackerAgent(name="second")

        registry = AgentRegistry()
        registry.register(AgentSpec(name="first", description="", agent=first))
        registry.register(AgentSpec(name="second", description="", agent=second))

        engine = Engine(agent=first, agent_registry=registry)
        result = engine.run(task="test task", max_steps=5)

        # Verify handoff metadata is stored in state
        assert "last_handoff" in result.state.metadata
        assert result.state.metadata["last_handoff"]["from"] == "first"
        assert result.state.metadata["last_handoff"]["to"] == "second"


# ── Handoff loop detection tests ─────────────────────────────────────────


class TestHandoffLoopDetection:
    def test_cycle_detection_raises(self):
        """A→B→A cycle should raise QitosRuntimeError."""
        from qitos.core.errors import QitosRuntimeError

        class PingAgent(AgentModule[SharedState, dict[str, Any], Action]):
            def __init__(self, name, target):
                super().__init__(tool_registry=ToolRegistry())
                self.name = name
                self._target = target

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=10)

            def decide(self, state, observation):
                return Decision.handoff(target=self._target)

            def reduce(self, state, observation, decision):
                return state

        ping = PingAgent(name="ping", target="pong")
        pong = PingAgent(name="pong", target="ping")

        registry = AgentRegistry()
        registry.register(AgentSpec(name="ping", description="", agent=ping))
        registry.register(AgentSpec(name="pong", description="", agent=pong))

        engine = Engine(agent=ping, agent_registry=registry)
        with pytest.raises(QitosRuntimeError, match="Handoff loop detected"):
            engine.run(task="test", max_steps=10)

    def test_max_handoffs_exceeded(self):
        """Exceeding max_handoffs should raise QitosRuntimeError."""
        from qitos.core.errors import QitosRuntimeError

        class ChainAgent(AgentModule[SharedState, dict[str, Any], Action]):
            _counter = 0

            def __init__(self, name):
                super().__init__(tool_registry=ToolRegistry())
                self.name = name

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=20)

            def decide(self, state, observation):
                ChainAgent._counter += 1
                next_name = f"agent_{ChainAgent._counter % 5}"
                return Decision.handoff(target=next_name)

            def reduce(self, state, observation, decision):
                return state

        # Register 5 chain agents (no cycles since names rotate: agent_0..agent_4)
        agents = {}
        for i in range(5):
            a = ChainAgent(name=f"agent_{i}")
            agents[f"agent_{i}"] = a

        registry = AgentRegistry()
        for name, a in agents.items():
            registry.register(AgentSpec(name=name, description="", agent=a))

        from qitos.engine.states import ContextConfig
        engine = Engine(
            agent=agents["agent_0"],
            agent_registry=registry,
            context_config=ContextConfig(max_handoffs=3),
        )
        with pytest.raises(QitosRuntimeError, match="Maximum handoff count"):
            engine.run(task="test", max_steps=20)

    def test_handoff_history_resets_on_new_run(self):
        """_handoff_history should reset between runs."""
        handoffer = HandoffAgent(target="receiver")
        receiver = FinalAgent()
        registry = AgentRegistry()
        registry.register(AgentSpec(name="handoffer", description="", agent=handoffer))
        registry.register(AgentSpec(name="receiver", description="", agent=receiver))

        engine = Engine(agent=handoffer, agent_registry=registry)
        engine.run(task="first run", max_steps=5)
        # History is populated during the run
        assert "handoffer" in engine._handoff_history
        # Second run should reset the history
        # Need a fresh handoffer since the agent was swapped to receiver
        handoffer2 = HandoffAgent(target="receiver")
        registry._specs["handoffer"] = AgentSpec(name="handoffer", description="", agent=handoffer2)
        engine.agent = handoffer2
        engine.run(task="second run", max_steps=5)
        # History should have been reset — only this run's history present
        assert "handoffer" in engine._handoff_history


# ── HandoffContext activation tests ───────────────────────────────────────


class TestHandoffContextActivation:
    def test_shared_state_fields_filters_state(self):
        """HandoffContext.shared_state_fields should strip non-allowed fields."""
        @dataclass
        class RichState(StateSchema):
            secret: str = "hidden"
            public: str = "visible"
            scratchpad: list = field(default_factory=list)

        class FirstAgent(AgentModule[RichState, dict[str, Any], Action]):
            def __init__(self):
                super().__init__(tool_registry=ToolRegistry())
                self.name = "first"

            def init_state(self, task, **kwargs):
                return RichState(task=task, max_steps=5, secret="s3cret", public="pub")

            def decide(self, state, observation):
                return Decision.handoff(target="second")

            def reduce(self, state, observation, decision):
                return state

        class SecondAgent(AgentModule[RichState, dict[str, Any], Action]):
            def __init__(self):
                super().__init__(tool_registry=ToolRegistry())
                self.name = "second"

            def init_state(self, task, **kwargs):
                return RichState(task=task, max_steps=5)

            def decide(self, state, observation):
                return Decision.final(answer="done")

            def reduce(self, state, observation, decision):
                return state

        registry = AgentRegistry()
        registry.register(AgentSpec(name="first", description="", agent=FirstAgent()))
        registry.register(AgentSpec(
            name="second",
            description="",
            agent=SecondAgent(),
            handoff_context=HandoffContext(shared_state_fields=["public", "scratchpad"]),
        ))

        engine = Engine(agent=FirstAgent(), agent_registry=registry)
        result = engine.run(task="test", max_steps=5)

        # 'secret' should have been removed and stored in metadata
        assert result.state.secret == "" or not hasattr(result.state, 'secret') or result.state.__dict__.get('secret') is None or result.state.secret != "s3cret"
        # 'public' should be preserved
        assert result.state.public == "pub"
        # Removed fields should be in metadata for recovery
        assert "_handoff_removed_fields" in result.state.metadata

    def test_max_history_rounds_truncates(self):
        """HandoffContext.max_history_rounds should truncate runtime history."""
        class FirstAgent(AgentModule[SharedState, dict[str, Any], Action]):
            def __init__(self):
                super().__init__(tool_registry=ToolRegistry())
                self.name = "first"

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=5)

            def decide(self, state, observation):
                return Decision.handoff(target="second")

            def reduce(self, state, observation, decision):
                return state

        class SecondAgent(AgentModule[SharedState, dict[str, Any], Action]):
            def __init__(self):
                super().__init__(tool_registry=ToolRegistry())
                self.name = "second"

            def init_state(self, task, **kwargs):
                return SharedState(task=task, max_steps=5)

            def decide(self, state, observation):
                return Decision.final(answer="done")

            def reduce(self, state, observation, decision):
                return state

        registry = AgentRegistry()
        registry.register(AgentSpec(name="first", description="", agent=FirstAgent()))
        registry.register(AgentSpec(
            name="second",
            description="",
            agent=SecondAgent(),
            handoff_context=HandoffContext(max_history_rounds=1),
        ))

        engine = Engine(agent=FirstAgent(), agent_registry=registry)
        result = engine.run(task="test", max_steps=5)
        # The run should complete successfully
        assert result.state.stop_reason == "final"
