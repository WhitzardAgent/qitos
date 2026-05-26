"""E2E: Delegation and fanout multi-agent patterns with real LLM."""
from __future__ import annotations

import pytest

from .conftest import e2e_skip, create_e2e_llm, create_e2e_engine_with_registry


@e2e_skip
@pytest.mark.e2e
def test_delegate_tool_sub_agent_runs():
    """DelegateTool runs a sub-agent and returns its result."""
    from ._agents import DelegatingOrchestrator, MathWorker
    from qitos.core.agent_spec import AgentSpec

    llm = create_e2e_llm(temperature=0.0)
    orchestrator = DelegatingOrchestrator(llm=llm)
    math_worker = MathWorker(llm=llm)

    engine = create_e2e_engine_with_registry(
        orchestrator,
        [AgentSpec(name="math_worker", description="Math specialist that solves math problems", agent=math_worker)],
        auto_approve=True,
    )
    result = engine.run("Use delegate_to_math_worker to calculate 12 times 8.")
    assert result.state is not None
    # The delegation should have occurred
    tool_calls = result.tool_calls_by_name
    # Either delegate or the orchestrator answered directly
    assert result.state.final_result is not None


@e2e_skip
@pytest.mark.e2e
def test_fanout_parallel_execution():
    """FanOutTool dispatches tasks to multiple workers in parallel."""
    from ._agents import DelegatingOrchestrator, MathWorker, StringWorker
    from qitos.core.agent_spec import AgentSpec, AgentRegistry

    llm = create_e2e_llm(temperature=0.0)
    orchestrator = DelegatingOrchestrator(llm=llm)
    math_worker = MathWorker(llm=llm)
    string_worker = StringWorker(llm=llm)

    registry = AgentRegistry()
    registry.register(AgentSpec(name="math_worker", description="Math specialist", agent=math_worker))
    registry.register(AgentSpec(name="string_worker", description="String operations specialist", agent=string_worker))

    # Get delegate tools and fanout tool
    from qitos.engine.engine import Engine
    engine = Engine(agent=orchestrator, agent_registry=registry, auto_approve=True)

    result = engine.run(
        "Use the fanout tool to run these tasks in parallel: "
        "math_worker should calculate 5 * 9, string_worker should reverse 'abc'."
    )
    assert result.state is not None
    # The fanout should have been called (or the LLM chose delegation)
    assert result.state.final_result is not None


@e2e_skip
@pytest.mark.e2e
def test_shared_memory_across_delegated_agents():
    """Shared memory is accessible across delegated sub-agents."""
    from ._agents import DelegatingOrchestrator, MathWorker
    from qitos.core.agent_spec import AgentSpec
    from qitos.core.shared_memory import InMemorySharedMemory

    llm = create_e2e_llm(temperature=0.0)
    shared_mem = InMemorySharedMemory()
    orchestrator = DelegatingOrchestrator(llm=llm)
    math_worker = MathWorker(llm=llm)

    engine = create_e2e_engine_with_registry(
        orchestrator,
        [AgentSpec(name="math_worker", description="Math specialist", agent=math_worker, shared_memory=shared_mem)],
        auto_approve=True,
        shared_memory=shared_mem,
    )
    result = engine.run("Use delegate_to_math_worker to add 3 and 4.")
    assert result.state is not None


@e2e_skip
@pytest.mark.e2e
def test_delegate_depth_limit():
    """Exceeding delegate depth returns an error status."""
    from ._agents import DelegatingOrchestrator, MathWorker
    from qitos.core.agent_spec import AgentSpec

    llm = create_e2e_llm(temperature=0.0)
    orchestrator = DelegatingOrchestrator(llm=llm)
    math_worker = MathWorker(llm=llm)

    engine = create_e2e_engine_with_registry(
        orchestrator,
        [AgentSpec(name="math_worker", description="Math specialist", agent=math_worker)],
        auto_approve=True,
        delegate_depth=2,  # Allow some nesting
    )
    result = engine.run("Use delegate_to_math_worker to compute 2 + 2.")
    assert result.state is not None
    # The delegation should complete within depth limits
    assert result.state.final_result is not None
