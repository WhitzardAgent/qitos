"""E2E: Step-by-step REPL API — init_session, step, advance_step, submit_turn."""
from __future__ import annotations

import pytest

from .conftest import e2e_skip, create_e2e_llm, create_e2e_engine


@e2e_skip
@pytest.mark.e2e
def test_init_session_returns_valid_state():
    """init_session returns a state with the correct task and step 0."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    state, observation = engine.init_session("What is 3 + 4?")
    assert state is not None
    assert "3 + 4" in state.task or "3+4" in state.task or "3" in state.task
    assert state.current_step == 0
    assert observation is not None


@e2e_skip
@pytest.mark.e2e
def test_step_produces_decision_and_advances():
    """A single step produces a valid decision with a mode."""
    from ._agents import CalculatorAgent
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(agent, auto_approve=True)
    state, observation = engine.init_session("What is 2 + 2?")
    step_result = engine.step(state, observation)
    assert step_result.decision is not None
    assert step_result.step_id == 0
    assert step_result.decision.mode in ("act", "final", "wait", "handoff")


@e2e_skip
@pytest.mark.e2e
def test_multi_step_manual_loop():
    """Manual step loop produces correct result for multi-step task."""
    from ._agents import CalculatorAgent
    from qitos.engine.states import RuntimeBudget
    llm = create_e2e_llm(temperature=0.0)
    agent = CalculatorAgent(llm=llm)
    engine = create_e2e_engine(
        agent,
        auto_approve=True,
        budget=RuntimeBudget(max_steps=5),
    )
    state, observation = engine.init_session("Multiply 6 by 7 using the multiply tool.")
    steps_taken = 0
    for _ in range(5):
        step_result = engine.step(state, observation)
        steps_taken += 1
        if step_result.stop:
            break
        # Update state and advance
        engine.advance_step(state)
        observation = step_result.observation

    # Agent should have computed 42 or stopped by budget
    final = getattr(state, "final_result", None) or ""
    assert "42" in str(final) or step_result.stop


@e2e_skip
@pytest.mark.e2e
def test_submit_turn_enables_multi_turn():
    """submit_turn appends a user message for multi-turn conversation."""
    from ._agents import SimpleReActAgent
    from qitos.engine.states import RuntimeBudget
    llm = create_e2e_llm(temperature=0.0)
    agent = SimpleReActAgent(llm=llm)
    engine = create_e2e_engine(
        agent,
        auto_approve=True,
        budget=RuntimeBudget(max_steps=6),
    )
    # First turn: ask a question
    state, observation = engine.init_session("What is the capital of France? Answer in one word.")
    for _ in range(4):
        step_result = engine.step(state, observation)
        if step_result.stop:
            break
        engine.advance_step(state)
        observation = step_result.observation

    first_answer = str(getattr(state, "final_result", "") or "")
    # Second turn: follow up
    state2, obs2 = engine.submit_turn(state, "Now tell me the capital of Germany in one word.")
    for _ in range(4):
        step_result2 = engine.step(state2, obs2)
        if step_result2.stop:
            break
        engine.advance_step(state2)
        obs2 = step_result2.observation

    second_answer = str(getattr(state2, "final_result", "") or "")
    # The second answer should be different and mention Berlin
    assert "berlin" in second_answer.lower() or "Berlin" in second_answer
