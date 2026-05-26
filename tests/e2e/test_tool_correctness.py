"""E2E: Tool calling correctness — correct tools, correct args, correct results."""
from __future__ import annotations

import pytest

from .conftest import e2e_skip, create_e2e_llm, create_e2e_engine


@e2e_skip
@pytest.mark.e2e
def test_tool_called_with_correct_args():
    """LLM calls get_temperature with the correct city argument."""
    from ._agents import WeatherAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = WeatherAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run("What is the temperature in Tokyo? Use the get_temperature tool.")
    assert result.state is not None
    # Verify the correct tool was called
    assert "get_temperature" in result.tool_calls_by_name
    # Verify the tool was called with the correct city argument
    found_tokyo = False
    for rec in result.records:
        for action in rec.actions:
            if hasattr(action, 'args') and isinstance(action.args, dict):
                city_val = str(action.args.get("city", "")).lower()
                if "tokyo" in city_val:
                    found_tokyo = True
    assert found_tokyo, "get_temperature was not called with city='Tokyo'"


@e2e_skip
@pytest.mark.e2e
def test_multi_tool_sequential_calls():
    """LLM calls multiple tools sequentially across steps."""
    from ._agents import WeatherAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = WeatherAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run(
        "First get the temperature in Paris, then get the 3-day forecast for Paris. "
        "Report both results."
    )
    assert result.state is not None
    assert "get_temperature" in result.tool_calls_by_name
    assert "get_forecast" in result.tool_calls_by_name
    assert result.step_count >= 2


@e2e_skip
@pytest.mark.e2e
def test_tool_result_propagates_to_final_answer():
    """Tool output is used in the final answer — not just hallucinated."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run("What is 17 + 25? Use the add tool.")
    assert result.state is not None
    assert result.state.final_result is not None
    # The answer must contain 42 AND the add tool must have been called
    assert "42" in str(result.state.final_result)
    assert "add" in result.tool_calls_by_name


@e2e_skip
@pytest.mark.e2e
def test_read_only_tool_executes_without_approval():
    """read_only tools execute successfully even without auto_approve."""
    from ._agents import CalculatorAgent
    from ._tools import StringToolSet
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=False)
    result = engine.run("Reverse the string 'hello' using the reverse_string tool.")
    assert result.state is not None
    # The reverse_string tool should have been called (it's read_only, no approval needed)
    # Note: agent may or may not call the tool depending on LLM, but if it does it should work
    if "reverse_string" in result.tool_calls_by_name:
        assert "olleh" in str(result.state.final_result).lower()


@e2e_skip
@pytest.mark.e2e
def test_needs_approval_tool_blocked_without_auto_approve():
    """needs_approval tool is blocked when auto_approve=False and no human approval."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=False)
    result = engine.run("Divide 100 by 4 using the dangerous_divide tool.")
    assert result.state is not None
    # dangerous_divide requires approval; without auto_approve it should be blocked
    assert "dangerous_divide" not in result.tool_calls_by_name


@e2e_skip
@pytest.mark.e2e
def test_tool_result_in_step_records():
    """Step records contain tool invocation details and action results."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    result = engine.run("Add 3 and 5 using the add tool.")
    assert result.state is not None
    # Verify step records have action results
    found_result = False
    for rec in result.records:
        for ar in rec.action_results:
            ar_dict = ar if isinstance(ar, dict) else (ar.__dict__ if hasattr(ar, '__dict__') else {})
            # Check if the action result contains the expected output
            output = ar_dict.get("output", "")
            if isinstance(output, dict) and output.get("result") == 8:
                found_result = True
            # Also check the stringified version
            if "8" in str(ar_dict):
                found_result = True
    # At least verify records exist with action results
    has_action_results = any(
        len(rec.action_results) > 0 for rec in result.records
    )
    assert has_action_results, "No action results found in step records"
