"""Tests for multi-agent pattern templates."""

from __future__ import annotations

import pytest

from qitos.kit.patterns import (
    ManagerWorkerConfig,
    PlannerExecutorConfig,
    ProposerVerifierConfig,
    build_manager_worker_system,
    build_planner_executor_system,
    build_proposer_verifier_system,
)


class TestManagerWorkerPattern:
    def test_build_returns_coordinator_and_registry(self):
        config = ManagerWorkerConfig(workspace_root="/tmp/test")
        coordinator, registry = build_manager_worker_system(config)
        assert coordinator is not None
        assert registry is not None

    def test_registry_has_worker(self):
        config = ManagerWorkerConfig(workspace_root="/tmp/test", worker_name="explorer")
        _, registry = build_manager_worker_system(config)
        names = [s.name for s in registry.list_available()]
        assert "explorer" in names

    def test_coordinator_has_fanout_tool(self):
        config = ManagerWorkerConfig(workspace_root="/tmp/test")
        coordinator, _ = build_manager_worker_system(config)
        tool_names = list(coordinator.tool_registry._tools.keys()) if hasattr(coordinator.tool_registry, '_tools') else []
        assert "fanout" in tool_names

    def test_custom_config(self):
        config = ManagerWorkerConfig(
            workspace_root="/tmp/test",
            worker_name="researcher",
            worker_max_steps=8,
            max_workers=6,
        )
        coordinator, registry = build_manager_worker_system(config)
        spec = registry.resolve("researcher")
        assert spec.max_steps_override == 8


class TestPlannerExecutorPattern:
    def test_build_returns_engine(self):
        config = PlannerExecutorConfig(workspace_root="/tmp/test")
        engine = build_planner_executor_system(config)
        assert engine is not None
        assert engine.agent_registry is not None

    def test_registry_has_both_agents(self):
        config = PlannerExecutorConfig(workspace_root="/tmp/test")
        engine = build_planner_executor_system(config)
        names = [s.name for s in engine.agent_registry.list_available()]
        assert "planner" in names
        assert "executor" in names

    def test_engine_starts_with_planner(self):
        config = PlannerExecutorConfig(workspace_root="/tmp/test")
        engine = build_planner_executor_system(config)
        assert engine.agent.__class__.__name__ == "PlannerAgent"

    def test_shared_state_fields(self):
        config = PlannerExecutorConfig(
            workspace_root="/tmp/test",
            shared_state_fields=["plan", "scratchpad"],
        )
        engine = build_planner_executor_system(config)
        spec = engine.agent_registry.resolve("executor")
        assert spec.handoff_context is not None
        assert "plan" in spec.handoff_context.shared_state_fields


class TestProposerVerifierPattern:
    def test_build_returns_proposer_and_registry(self):
        config = ProposerVerifierConfig(workspace_root="/tmp/test")
        proposer, registry = build_proposer_verifier_system(config)
        assert proposer is not None
        assert registry is not None

    def test_registry_has_verifier(self):
        config = ProposerVerifierConfig(workspace_root="/tmp/test")
        _, registry = build_proposer_verifier_system(config)
        names = [s.name for s in registry.list_available()]
        assert "verifier" in names

    def test_proposer_has_delegation_tools(self):
        config = ProposerVerifierConfig(workspace_root="/tmp/test")
        proposer, _ = build_proposer_verifier_system(config)
        tool_names = list(proposer.tool_registry._tools.keys()) if hasattr(proposer.tool_registry, '_tools') else []
        # Should have delegate_to_verifier and fanout
        assert any("delegate_to" in name for name in tool_names)
        assert "fanout" in tool_names

    def test_custom_names(self):
        config = ProposerVerifierConfig(
            workspace_root="/tmp/test",
            proposer_name="auditor",
            verifier_name="reviewer",
        )
        _, registry = build_proposer_verifier_system(config)
        names = [s.name for s in registry.list_available()]
        assert "reviewer" in names
