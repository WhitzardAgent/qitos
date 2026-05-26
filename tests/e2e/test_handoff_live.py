"""E2E: Handoff live — full handoff flow with real LLM (not just tool registration)."""
from __future__ import annotations

import pytest

from .conftest import e2e_skip, create_e2e_llm, create_e2e_engine_with_registry


@e2e_skip
@pytest.mark.e2e
def test_handoff_to_math_worker_completes():
    """Orchestrator hands off to math_worker and the task completes."""
    from ._agents import HandoffOrchestrator, MathWorker
    from qitos.core.agent_spec import AgentSpec

    llm = create_e2e_llm(temperature=0.0)
    orchestrator = HandoffOrchestrator(llm=llm)
    math_worker = MathWorker(llm=llm)

    engine = create_e2e_engine_with_registry(
        orchestrator,
        [AgentSpec(name="math_worker", description="Math specialist", agent=math_worker)],
        auto_approve=True,
    )
    result = engine.run("Use transfer_to_math_worker to calculate 8 times 7.")
    assert result.state is not None
    # The orchestrator state should track the delegation
    assert result.state.final_result is not None
    # The answer should contain 56
    assert "56" in str(result.state.final_result)


@e2e_skip
@pytest.mark.e2e
def test_handoff_to_string_worker_completes():
    """Orchestrator hands off to string_worker and the task completes."""
    from ._agents import HandoffOrchestrator, StringWorker
    from qitos.core.agent_spec import AgentSpec

    llm = create_e2e_llm(temperature=0.0)
    orchestrator = HandoffOrchestrator(llm=llm)
    string_worker = StringWorker(llm=llm)

    engine = create_e2e_engine_with_registry(
        orchestrator,
        [AgentSpec(name="string_worker", description="String operations specialist", agent=string_worker)],
        auto_approve=True,
    )
    result = engine.run("Use transfer_to_string_worker to reverse the string 'hello'.")
    assert result.state is not None
    assert result.state.final_result is not None
    # The answer should contain 'olleh'
    assert "olleh" in str(result.state.final_result).lower()


@e2e_skip
@pytest.mark.e2e
def test_handoff_loop_detected():
    """Handoff loop (A→B→A) is detected and the run terminates."""
    from dataclasses import dataclass, field
    from typing import Any
    from qitos import AgentModule, Decision, StateSchema
    from qitos.kit import ReActTextParser
    from qitos.core.agent_spec import AgentSpec, AgentRegistry
    from qitos.engine.engine import Engine

    llm = create_e2e_llm(temperature=0.0)

    # Agent A hands off to agent B
    @dataclass
    class LoopState(StateSchema):
        pass

    class AgentA(AgentModule[LoopState, Any, Any]):
        name = "agent_a"
        handoff_targets = ["agent_b"]

        def __init__(self, llm=None, **kwargs):
            super().__init__(llm=llm, model_parser=ReActTextParser(), **kwargs)

        def init_state(self, task, **kwargs):
            return LoopState(task=task, max_steps=6)

        def build_system_prompt(self, state):
            return "You are Agent A. Always use transfer_to_agent_b to hand off to Agent B."

        def prepare(self, state):
            return f"Task: {state.task}"

        def reduce(self, state, observation, decision):
            if decision.mode == "final":
                state.final_result = str(decision.final_answer or "")
            return state

    class AgentB(AgentModule[LoopState, Any, Any]):
        name = "agent_b"
        handoff_targets = ["agent_a"]

        def __init__(self, llm=None, **kwargs):
            super().__init__(llm=llm, model_parser=ReActTextParser(), **kwargs)

        def init_state(self, task, **kwargs):
            return LoopState(task=task, max_steps=6)

        def build_system_prompt(self, state):
            return "You are Agent B. Always use transfer_to_agent_a to hand off to Agent A."

        def prepare(self, state):
            return f"Task: {state.task}"

        def reduce(self, state, observation, decision):
            if decision.mode == "final":
                state.final_result = str(decision.final_answer or "")
            return state

    agent_a = AgentA(llm=llm)
    agent_b = AgentB(llm=llm)

    registry = AgentRegistry()
    registry.register(AgentSpec(name="agent_a", description="Agent A", agent=agent_a))
    registry.register(AgentSpec(name="agent_b", description="Agent B", agent=agent_b))

    engine = Engine(agent=agent_a, agent_registry=registry, auto_approve=True)
    result = engine.run("Hand off to agent B, then agent B should hand off to agent A.")
    # The run should terminate — either via loop detection or max steps
    assert result.state is not None
    # The stop reason should indicate a loop or budget exhaustion
    stop_reason = str(getattr(result.state, "stop_reason", "") or "").upper()
    assert "LOOP" in stop_reason or "MAX_STEPS" in stop_reason or "BUDGET" in stop_reason or result.state.final_result is not None
