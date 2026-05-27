"""Tests for EngineConfig export."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from qitos.engine.states import EngineConfig


class TestEngineConfig:
    def test_default_values(self) -> None:
        config = EngineConfig()
        assert config.agent_name == ""
        assert config.model_id == ""
        assert config.budget_max_steps == 10
        assert config.budget_max_runtime_seconds is None
        assert config.budget_max_tokens is None
        assert config.critic_names == []
        assert config.stop_criteria_names == []
        assert config.has_checkpoint_store is False
        assert config.has_tracing_provider is False
        assert config.protocol_id is None
        assert config.delegate_depth == 0
        assert config.has_shared_memory is False
        assert config.has_env is False
        assert config.tool_count == 0

    def test_frozen(self) -> None:
        config = EngineConfig()
        with pytest.raises(FrozenInstanceError):
            config.agent_name = "test"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        config = EngineConfig(
            agent_name="my-agent",
            model_id="gpt-4o",
            budget_max_steps=20,
            critic_names=["ScoreCritic"],
            has_tracing_provider=True,
        )
        d = config.to_dict()
        assert d["agent_name"] == "my-agent"
        assert d["model_id"] == "gpt-4o"
        assert d["budget_max_steps"] == 20
        assert d["critic_names"] == ["ScoreCritic"]
        assert d["has_tracing_provider"] is True
        # All fields should be present
        assert "stop_criteria_names" in d
        assert "tool_count" in d

    def test_to_dict_matches_field_count(self) -> None:
        config = EngineConfig()
        d = config.to_dict()
        assert len(d) == len(EngineConfig.__dataclass_fields__)
