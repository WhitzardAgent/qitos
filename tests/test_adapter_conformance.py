"""Conformance tests for the QitOS Adapter SDK export APIs.

These tests verify that the export APIs (EngineConfig, ToolPermissionSpec,
CriticTrace, HandoffTrace) work end-to-end and produce correct, serializable
output suitable for adapter consumption (e.g., Snowl QitOSAdapter).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from qitos.core.agent_module import AgentModule
from qitos.core.state import StateSchema
from qitos.core.tool import ToolMeta, ToolPermission, ToolPermissionSpec
from qitos.core.tool_registry import ToolRegistry
from qitos.engine.engine import Engine, EngineResult
from qitos.engine.states import (
    CriticTrace,
    EngineConfig,
    HandoffTrace,
    RuntimeEvent,
    RuntimePhase,
    StepRecord,
)


# ---- Helpers ----


class _ConcreteAgent(AgentModule):
    """Minimal concrete AgentModule for testing."""

    def init_state(self):
        return StateSchema()

    def reduce(self, decision, state=None):
        return state or {}


def _make_record(
    step_id: int,
    critic_outputs: List[Dict[str, Any]] | None = None,
) -> StepRecord:
    return StepRecord(
        step_id=step_id,
        critic_outputs=critic_outputs or [],
    )


def _make_handoff_event(
    step_id: int,
    from_agent: str = "agent_a",
    to_agent: str = "agent_b",
    context_strategy: str = "FULL",
    messages_passed: int = 5,
) -> RuntimeEvent:
    return RuntimeEvent(
        step_id=step_id,
        phase=RuntimePhase.HANDOFF_START,
        payload={
            "from": from_agent,
            "to": to_agent,
            "context_strategy": context_strategy,
            "messages_passed": messages_passed,
        },
    )


# ---- EngineConfig conformance ----


class TestEngineConfigConformance:
    """Verify Engine.export_config() produces complete, serializable output."""

    def test_export_config_returns_engine_config(self):
        """Engine.export_config() must return an EngineConfig instance."""
        agent = _ConcreteAgent(llm=None)
        engine = Engine(agent=agent)
        config = engine.export_config()
        assert isinstance(config, EngineConfig)

    def test_export_config_all_fields_present(self):
        """All EngineConfig fields must be populated."""
        agent = _ConcreteAgent(llm=None)
        engine = Engine(agent=agent)
        config = engine.export_config()
        d = config.to_dict()
        for field_name in EngineConfig.__dataclass_fields__:
            assert field_name in d, f"Missing field: {field_name}"

    def test_export_config_serializable(self):
        """EngineConfig output must be JSON-serializable."""
        agent = _ConcreteAgent(llm=None)
        engine = Engine(agent=agent)
        config = engine.export_config()
        serialized = json.dumps(config.to_dict(), ensure_ascii=False)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["budget_max_steps"] == config.budget_max_steps

    def test_export_config_reflects_agent_name(self):
        """export_config must reflect the agent's name."""
        from qitos.core.agent_module import AgentModule

        agent = _ConcreteAgent(llm=None)
        agent.name = "test_agent"
        engine = Engine(agent=agent)
        config = engine.export_config()
        assert config.agent_name == "test_agent"


# ---- ToolPermissionSpec conformance ----


class TestToolPermissionSpecConformance:
    """Verify ToolRegistry.export_permissions() produces complete, serializable output."""

    def test_export_permissions_returns_list_of_specs(self):
        registry = ToolRegistry()

        def tool_a(x: str) -> str:
            """Tool A."""
            return x

        registry.register(tool_a)
        specs = registry.export_permissions()
        assert isinstance(specs, list)
        assert len(specs) == 1
        assert isinstance(specs[0], ToolPermissionSpec)

    def test_export_permissions_all_fields_serializable(self):
        registry = ToolRegistry()

        def my_tool(query: str) -> str:
            """Search."""
            return query

        meta = ToolMeta(
            name="my_tool",
            permissions=ToolPermission(network=True),
            needs_approval=True,
            read_only=False,
        )
        registry.register(my_tool, meta=meta)
        specs = registry.export_permissions()
        d = specs[0].to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["name"] == "my_tool"
        assert parsed["permissions"]["network"] is True
        assert parsed["needs_approval"] is True

    def test_export_permissions_maps_approval_flags(self):
        """needs_approval must be correctly mapped for adapter consumption."""
        registry = ToolRegistry()

        def safe_tool() -> str:
            """Safe."""
            return ""

        def dangerous_tool(cmd: str) -> str:
            """Dangerous."""
            return ""

        registry.register(safe_tool, meta=ToolMeta(needs_approval=False, read_only=True))
        registry.register(dangerous_tool, meta=ToolMeta(needs_approval=True, read_only=False))

        specs = registry.export_permissions()
        by_name = {s.name: s for s in specs}
        assert by_name["safe_tool"].needs_approval is False
        assert by_name["safe_tool"].read_only is True
        assert by_name["dangerous_tool"].needs_approval is True
        assert by_name["dangerous_tool"].read_only is False


# ---- CriticTrace conformance ----


class TestCriticTraceConformance:
    """Verify CriticTrace extraction from EngineResult works for adapter consumption."""

    def test_critic_trace_serializable(self):
        ct = CriticTrace(
            step_id=1,
            critic_name="ScoreCritic",
            action="retry",
            reason="low score",
            score=0.3,
            details={"attempts": 2},
            instruction_patch="Be more precise",
            state_patch={"retry_count": 1},
        )
        d = ct.to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["step_id"] == 1
        assert parsed["critic_name"] == "ScoreCritic"
        assert parsed["action"] == "retry"
        assert parsed["score"] == 0.3
        assert parsed["instruction_patch"] == "Be more precise"

    def test_critic_trace_in_engine_result_to_dict(self):
        """EngineResult.to_dict() must include critic_traces."""
        ct = CriticTrace(step_id=0, critic_name="C", action="continue", score=0.8)
        result = EngineResult(
            state=StateSchema(),
            records=[],
            events=[],
            step_count=1,
            critic_traces=[ct],
        )
        d = result.to_dict()
        assert "critic_traces" in d
        assert len(d["critic_traces"]) == 1
        assert d["critic_traces"][0]["action"] == "continue"

    def test_critic_trace_extraction_from_step_records(self):
        """Verify extraction logic maps StepRecord.critic_outputs to CriticTrace."""
        records = [
            _make_record(0, [
                {"critic_name": "ScoreCritic", "action": "continue", "reason": "ok", "score": 0.9},
            ]),
            _make_record(1, []),
            _make_record(2, [
                {"critic_name": "Verify", "action": "retry", "reason": "low", "score": 0.4,
                 "instruction_patch": "Be careful", "state_patch": {"x": 1}},
            ]),
        ]
        traces: List[CriticTrace] = []
        for record in records:
            for output in record.critic_outputs:
                if not isinstance(output, dict):
                    continue
                traces.append(CriticTrace(
                    step_id=record.step_id,
                    critic_name=str(output.get("critic_name", "unknown")),
                    action=str(output.get("action", "continue")),
                    reason=str(output.get("reason", "")),
                    score=float(output.get("score", 1.0)),
                    details=output.get("details", {}),
                    instruction_patch=output.get("instruction_patch"),
                    state_patch=output.get("state_patch"),
                ))

        assert len(traces) == 2
        assert traces[0].critic_name == "ScoreCritic"
        assert traces[1].instruction_patch == "Be careful"
        assert traces[1].state_patch == {"x": 1}

        # Verify all serializable
        for t in traces:
            json.dumps(t.to_dict(), ensure_ascii=False)


# ---- HandoffTrace conformance ----


class TestHandoffTraceConformance:
    """Verify HandoffTrace extraction from EngineResult works for adapter consumption."""

    def test_handoff_trace_serializable(self):
        ht = HandoffTrace(
            step_id=2,
            from_agent="orchestrator",
            to_agent="worker",
            context_strategy="SUMMARY",
            messages_passed=3,
        )
        d = ht.to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["step_id"] == 2
        assert parsed["from_agent"] == "orchestrator"
        assert parsed["to_agent"] == "worker"
        assert parsed["context_strategy"] == "SUMMARY"

    def test_handoff_trace_in_engine_result_to_dict(self):
        """EngineResult.to_dict() must include handoff_traces."""
        ht = HandoffTrace(step_id=1, from_agent="a", to_agent="b", context_strategy="FULL")
        result = EngineResult(
            state=StateSchema(),
            records=[],
            events=[],
            step_count=1,
            handoff_traces=[ht],
        )
        d = result.to_dict()
        assert "handoff_traces" in d
        assert len(d["handoff_traces"]) == 1
        assert d["handoff_traces"][0]["from_agent"] == "a"

    def test_handoff_trace_extraction_from_events(self):
        """Verify extraction logic maps RuntimeEvent HANDOFF_START to HandoffTrace."""
        events = [
            _make_handoff_event(1, "planner", "coder", "FULL", 5),
            RuntimeEvent(step_id=2, phase=RuntimePhase.DECIDE, payload={}),
            _make_handoff_event(3, "coder", "reviewer", "SUMMARY", 3),
        ]
        traces: List[HandoffTrace] = []
        for event in events:
            if event.phase != RuntimePhase.HANDOFF_START:
                continue
            payload = event.payload or {}
            traces.append(HandoffTrace(
                step_id=event.step_id,
                from_agent=str(payload.get("from", "")),
                to_agent=str(payload.get("to", "")),
                context_strategy=str(payload.get("context_strategy", "")),
                messages_passed=int(payload.get("messages_passed", 0)),
            ))

        assert len(traces) == 2
        assert traces[0].from_agent == "planner"
        assert traces[0].to_agent == "coder"
        assert traces[1].context_strategy == "SUMMARY"

        # Verify all serializable
        for t in traces:
            json.dumps(t.to_dict(), ensure_ascii=False)


# ---- Cross-cutting: combined result serialization ----


class TestCombinedResultConformance:
    """Verify a full EngineResult with traces is round-trip serializable."""

    def test_full_result_with_traces_serializable(self):
        ct = CriticTrace(step_id=0, critic_name="C1", action="continue", score=0.9)
        ht = HandoffTrace(step_id=1, from_agent="a", to_agent="b", context_strategy="FULL")
        result = EngineResult(
            state=StateSchema(),
            records=[_make_record(0, [{"critic_name": "C1", "action": "continue", "score": 0.9}])],
            events=[_make_handoff_event(1, "a", "b")],
            step_count=2,
            critic_traces=[ct],
            handoff_traces=[ht],
        )
        d = result.to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert "critic_traces" in parsed
        assert "handoff_traces" in parsed
        assert len(parsed["critic_traces"]) == 1
        assert len(parsed["handoff_traces"]) == 1

    def test_empty_traces_still_serializable(self):
        result = EngineResult(
            state=StateSchema(),
            records=[],
            events=[],
            step_count=0,
        )
        d = result.to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["critic_traces"] == []
        assert parsed["handoff_traces"] == []
