"""Tests for multi-agent evaluation utilities."""

from __future__ import annotations

from qitos.evaluate.multi_agent import per_agent_breakdown, handoff_metrics, delegation_metrics


class TestPerAgentBreakdown:
    def test_empty_steps(self):
        result = per_agent_breakdown([])
        assert result == {}

    def test_single_agent(self):
        steps = [
            {"agent_id": "planner", "metadata": {"tokens_in": 100, "tokens_out": 50}, "cost": 0.01},
            {"agent_id": "planner", "metadata": {"tokens_in": 200, "tokens_out": 80}, "cost": 0.02},
        ]
        result = per_agent_breakdown(steps)
        assert "planner" in result
        assert result["planner"]["step_count"] == 2
        assert result["planner"]["tokens_in"] == 300
        assert result["planner"]["tokens_out"] == 130
        assert result["planner"]["cost"] == 0.03

    def test_multiple_agents(self):
        steps = [
            {"agent_id": "triage", "metadata": {"tokens_in": 50, "tokens_out": 20}, "cost": 0.005},
            {"agent_id": "coder", "metadata": {"tokens_in": 200, "tokens_out": 100}, "cost": 0.02},
            {"agent_id": "coder", "metadata": {"tokens_in": 150, "tokens_out": 80}, "cost": 0.015},
        ]
        result = per_agent_breakdown(steps)
        assert len(result) == 2
        assert result["triage"]["step_count"] == 1
        assert result["coder"]["step_count"] == 2

    def test_default_agent_for_missing_id(self):
        steps = [{"metadata": {}, "cost": 0}]
        result = per_agent_breakdown(steps)
        assert "default" in result


class TestHandoffMetrics:
    def test_no_handoffs(self):
        steps = [{"agent_id": "agent_a"}, {"agent_id": "agent_a"}]
        result = handoff_metrics(steps)
        assert result["handoff_count"] == 0
        assert result["unique_agents"] == 1

    def test_one_handoff(self):
        steps = [
            {"agent_id": "triage"},
            {"agent_id": "triage"},
            {"agent_id": "coder"},
            {"agent_id": "coder"},
        ]
        result = handoff_metrics(steps)
        assert result["handoff_count"] == 1
        assert result["unique_agents"] == 2
        assert result["agent_sequence"] == ["triage", "coder"]
        assert result["handoff_overhead_steps"] == 1

    def test_multiple_handoffs(self):
        steps = [
            {"agent_id": "triage"},
            {"agent_id": "coder"},
            {"agent_id": "reviewer"},
        ]
        result = handoff_metrics(steps)
        assert result["handoff_count"] == 2
        assert result["unique_agents"] == 3


class TestDelegationMetrics:
    def test_no_delegations(self):
        result = delegation_metrics([])
        assert result["delegate_count"] == 0
        assert result["fanout_count"] == 0

    def test_delegate_events(self):
        events = [
            {"phase": "DELEGATE_END", "payload": {"status": "success"}},
            {"phase": "DELEGATE_END", "payload": {"status": "error"}},
        ]
        result = delegation_metrics(events)
        assert result["delegate_count"] == 2
        assert result["delegate_success_rate"] == 0.5

    def test_fanout_events(self):
        events = [
            {"phase": "FANOUT_START", "payload": {"task_count": 3}},
            {"phase": "FANOUT_END", "payload": {"total": 3, "succeeded": 3}},
        ]
        result = delegation_metrics(events)
        assert result["fanout_count"] == 1
        assert result["fanout_avg_tasks"] == 3.0
        assert result["fanout_success_rate"] == 1.0
