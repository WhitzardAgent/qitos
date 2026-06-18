"""Tests for the @critic decorator migration of pentagi cyber-agent critics.

Verifies that:
- Each @critic function is a valid Critic instance (isinstance check)
- Each @critic function produces the same behaviour as its class counterpart
- GracefulShutdownCritic functional version triggers in shutdown zone
- StuckDetectionCritic functional version detects identical loops
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from qitos.engine.critic import Critic
from qitos.engine.critic_result import CriticResult


# ---------------------------------------------------------------------------
# Lazy import: add qitos-cyber-agent to sys.path so pentagi is importable
# ---------------------------------------------------------------------------

_AGENT_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "plans",
    "qitos_zoo_migration",
    "apps",
    "qitos-cyber-agent",
)


def _import_critic_module():
    """Import the pentagi.critic package, adding the agent dir to sys.path."""
    agent_dir = os.path.abspath(_AGENT_DIR)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    from pentagi.critic import (
        GracefulShutdownCritic,
        ReflectorCritic,
        StuckDetectionCritic,
        make_graceful_shutdown_critic,
        make_reflector_critic,
        make_stuck_detection_critic,
    )
    return (
        GracefulShutdownCritic,
        ReflectorCritic,
        StuckDetectionCritic,
        make_graceful_shutdown_critic,
        make_reflector_critic,
        make_stuck_detection_critic,
    )


(
    GracefulShutdownCritic,
    ReflectorCritic,
    StuckDetectionCritic,
    make_graceful_shutdown_critic,
    make_reflector_critic,
    make_stuck_detection_critic,
) = (None, None, None, None, None, None)
try:
    (
        GracefulShutdownCritic,
        ReflectorCritic,
        StuckDetectionCritic,
        make_graceful_shutdown_critic,
        make_reflector_critic,
        make_stuck_detection_critic,
    ) = _import_critic_module()
except (ImportError, ModuleNotFoundError) as exc:
    pytest.skip(f"qitos-cyber-agent migration package is unavailable: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(**kw):
    return SimpleNamespace(**kw)


def _decision(actions=None, thought=""):
    return SimpleNamespace(actions=actions, thought=thought)


# ---------------------------------------------------------------------------
# isinstance checks — every @critic result must be a Critic
# ---------------------------------------------------------------------------

class TestCriticIsinstance:
    def test_reflector_func_is_critic(self):
        c = make_reflector_critic()
        assert isinstance(c, Critic)

    def test_stuck_detection_func_is_critic(self):
        c = make_stuck_detection_critic()
        assert isinstance(c, Critic)

    def test_graceful_shutdown_func_is_critic(self):
        c = make_graceful_shutdown_critic()
        assert isinstance(c, Critic)


# ---------------------------------------------------------------------------
# ReflectorCritic — class vs func parity
# ---------------------------------------------------------------------------

class TestReflectorParity:
    def test_continue_when_actions_present(self):
        state = _state()
        decision = _decision(actions=[{"tool": "done", "args": {}}])
        cls_result = ReflectorCritic().evaluate(state, decision, [])
        func_result = make_reflector_critic().evaluate(state, decision, [])
        assert cls_result.action == "continue"
        assert func_result.action == "continue"

    def test_continue_when_final_result_set(self):
        state = _state(final_result="done")
        decision = _decision(actions=None)
        cls_result = ReflectorCritic().evaluate(state, decision, [])
        func_result = make_reflector_critic().evaluate(state, decision, [])
        assert cls_result.action == "continue"
        assert func_result.action == "continue"

    def test_retry_on_free_text(self):
        state = _state()
        decision = _decision(actions=None, thought="I think the answer is 42")
        cls_result = ReflectorCritic().evaluate(state, decision, [])
        func_result = make_reflector_critic().evaluate(state, decision, [])
        assert cls_result.action == "retry"
        assert func_result.action == "retry"
        assert func_result.instruction_patch is not None

    def test_stop_after_max_retries(self):
        cls_critic = ReflectorCritic(max_retries=2)
        func_critic = make_reflector_critic(max_retries=2)
        state = _state()
        decision = _decision(actions=None, thought="free text")

        for _ in range(3):
            cls_critic.evaluate(state, decision, [])
            func_critic.evaluate(state, decision, [])

        cls_result = cls_critic.evaluate(state, decision, [])
        func_result = func_critic.evaluate(state, decision, [])
        assert cls_result.action == "stop"
        assert func_result.action == "stop"

    def test_reset_retry_count_on_tool_call(self):
        func_critic = make_reflector_critic(max_retries=2)
        state = _state()
        free_decision = _decision(actions=None, thought="text")
        tool_decision = _decision(actions=[{"tool": "done", "args": {}}])

        # Two free-text retries
        func_critic.evaluate(state, free_decision, [])
        func_critic.evaluate(state, free_decision, [])
        # A tool call resets the counter
        func_critic.evaluate(state, tool_decision, [])
        # Now free text again should retry (not stop), since counter was reset
        result = func_critic.evaluate(state, free_decision, [])
        assert result.action == "retry"


# ---------------------------------------------------------------------------
# StuckDetectionCritic — class vs func parity
# ---------------------------------------------------------------------------

class TestStuckDetectionParity:
    def test_continue_when_final_result_set(self):
        state = _state(final_result="done")
        decision = _decision(actions=[{"tool": "run", "args": {"cmd": "ls"}}])
        cls_result = StuckDetectionCritic().evaluate(state, decision, [])
        func_result = make_stuck_detection_critic().evaluate(state, decision, [])
        assert cls_result.action == "continue"
        assert func_result.action == "continue"

    def test_detects_identical_loop(self):
        cls_critic = StuckDetectionCritic(max_identical_actions=3)
        func_critic = make_stuck_detection_critic(max_identical_actions=3)
        state = _state(current_step=5)
        action = {"tool": "search", "args": {"query": "test"}}
        decision = _decision(actions=[action])

        # Feed identical actions
        for _ in range(3):
            cls_critic.evaluate(state, decision, [])
            func_critic.evaluate(state, decision, [])

        cls_result = cls_critic.evaluate(state, decision, [])
        func_result = func_critic.evaluate(state, decision, [])
        assert cls_result.action == "retry"
        assert func_result.action == "retry"
        assert func_result.state_patch == {"_stuck_detected": True}

    def test_continue_with_different_actions(self):
        func_critic = make_stuck_detection_critic(max_identical_actions=3)
        state = _state(current_step=5)

        for i in range(4):
            decision = _decision(actions=[{"tool": "search", "args": {"query": f"q{i}"}}])
            result = func_critic.evaluate(state, decision, [])
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# GracefulShutdownCritic — class vs func parity
# ---------------------------------------------------------------------------

class TestGracefulShutdownParity:
    def test_continue_when_final_result_set(self):
        state = _state(final_result="done", current_step=8, max_steps=10)
        decision = _decision()
        cls_result = GracefulShutdownCritic().evaluate(state, decision, [])
        func_result = make_graceful_shutdown_critic().evaluate(state, decision, [])
        assert cls_result.action == "continue"
        assert func_result.action == "continue"

    def test_continue_outside_shutdown_zone(self):
        state = _state(current_step=5, max_steps=10)
        decision = _decision()
        cls_result = GracefulShutdownCritic(shutdown_zone_steps=3).evaluate(state, decision, [])
        func_result = make_graceful_shutdown_critic(shutdown_zone_steps=3).evaluate(state, decision, [])
        assert cls_result.action == "continue"
        assert func_result.action == "continue"

    def test_triggers_in_shutdown_zone(self):
        state = _state(current_step=8, max_steps=10)
        decision = _decision()
        cls_result = GracefulShutdownCritic(shutdown_zone_steps=3).evaluate(state, decision, [])
        func_result = make_graceful_shutdown_critic(shutdown_zone_steps=3).evaluate(state, decision, [])
        assert cls_result.action == "retry"
        assert func_result.action == "retry"
        assert "2 step(s) remaining" in func_result.instruction_patch
        assert func_result.score == pytest.approx(0.1)

    def test_no_max_steps_means_continue(self):
        state = _state(current_step=8, max_steps=0)
        decision = _decision()
        func_result = make_graceful_shutdown_critic().evaluate(state, decision, [])
        assert func_result.action == "continue"

    def test_custom_shutdown_zone(self):
        # zone=5, step 7 of 10 => 3 remaining <= 5 => trigger
        state = _state(current_step=7, max_steps=10)
        decision = _decision()
        func_result = make_graceful_shutdown_critic(shutdown_zone_steps=5).evaluate(state, decision, [])
        assert func_result.action == "retry"

        # zone=5, step 4 of 10 => 6 remaining > 5 => continue
        state2 = _state(current_step=4, max_steps=10)
        func_result2 = make_graceful_shutdown_critic(shutdown_zone_steps=5).evaluate(state2, decision, [])
        assert func_result2.action == "continue"
