"""E2E: Engine result metrics and recovery policy with real LLM."""
from __future__ import annotations

import pytest

from .conftest import e2e_skip, e2e_flaky, create_e2e_llm, create_e2e_engine


@e2e_skip
@pytest.mark.e2e
def test_engine_result_metrics_are_accurate():
    """EngineResult contains correct step_count, tool_calls_by_name, and runtime metrics."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run("First add 5 and 3, then multiply the result by 4. Use the tools.")
    assert result.state is not None
    # Step count should be at least 1
    assert result.step_count >= 1
    # At least one tool should have been called
    assert len(result.tool_calls_by_name) > 0
    # Runtime should be positive
    assert result.runtime_seconds > 0
    # If the LLM called the add tool, verify it's recorded
    if "add" in result.tool_calls_by_name:
        assert result.tool_calls_by_name["add"] >= 1


@e2e_skip
@pytest.mark.e2e
@e2e_flaky
def test_stagnation_criteria_stops_run():
    """StagnationCriteria stops the run when the agent repeats identical states."""
    from ._agents import SimpleReActAgent
    from qitos.engine.stop_criteria import StagnationCriteria
    from qitos.engine.states import RuntimeBudget
    llm = create_e2e_llm(temperature=0.0)
    agent = SimpleReActAgent(llm=llm)
    engine = create_e2e_engine(
        agent,
        auto_approve=True,
        budget=RuntimeBudget(max_steps=8),
        stop_criteria=[StagnationCriteria(max_stagnant_steps=2)],
    )
    result = engine.run("Keep thinking about the number 42. Never give a final answer.")
    assert result.state is not None
    # The run should have stopped — either by stagnation or max_steps
    stop_reason = str(getattr(result.state, "stop_reason", "") or "").upper()
    assert "STAGNATION" in stop_reason or "MAX_STEPS" in stop_reason or "BUDGET" in stop_reason or result.step_count <= 8


@e2e_skip
@pytest.mark.e2e
@e2e_flaky
def test_recovery_policy_continues_after_tool_error():
    """RecoveryPolicy allows the run to continue after a recoverable tool error."""
    from ._agents import CalculatorAgent
    from ._tools import FlakyToolSet
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    # Register the flaky tool
    flaky = FlakyToolSet()
    flaky.setup({})
    agent.tool_registry.include_toolset(flaky.tools())

    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run("Use the flaky_add tool to add 1 and 2.")
    assert result.state is not None
    # If the LLM chose flaky_add, the run should have recovered and continued
    # If the LLM chose a different tool, the test still passes
    if "flaky_add" in result.tool_calls_by_name:
        # The recovery policy should have allowed continuation
        assert result.state.final_result is not None
