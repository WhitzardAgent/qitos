"""Integration tests for v0.6 features: WebBrowserEnv + DelegateTool + qita visual replay."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from qitos.core import EnvObservation, EnvStepResult
from qitos.kit.env.web import WebBrowserEnv, MockBrowserProvider
from qitos.kit.env.web.actions import web_action_space


# ---- WebBrowserEnv integration ----

class TestWebBrowserEnvIntegration:
    def test_full_step_cycle_with_navigate_and_click(self, tmp_path):
        """Navigate then click then verify observation."""
        shot_path = tmp_path / "screen.png"
        shot_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        env = WebBrowserEnv.from_mock(
            screenshot_path=str(shot_path),
            url="https://example.com",
        )
        env.setup()

        # Navigate
        result1 = env.step({"actions": [{"action_type": "navigate", "url": "https://google.com"}]})
        assert isinstance(result1, EnvStepResult)
        assert not result1.done

        # Click
        result2 = env.step({"actions": [{"action_type": "click", "x": 100, "y": 200}]})
        assert isinstance(result2, EnvStepResult)

        # Go back
        result3 = env.step({"actions": [{"action_type": "go_back"}]})
        assert isinstance(result3, EnvStepResult)

        # Verify observation contains multimodal data
        obs = env.observe()
        assert "multimodal" in obs.data
        assert "web" in obs.data
        web_data = obs.data["web"]
        assert web_data.get("url") is not None

        env.close()

    def test_action_space_roundtrip_serializable(self):
        """Action space to_dict() is JSON-serializable."""
        space = web_action_space()
        d = space.to_dict()
        serialized = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert "navigate" in parsed["allowed_actions"]
        assert "click" in parsed["allowed_actions"]


# ---- DelegateTool integration with AgentSpec ----

class TestDelegateToolIntegration:
    def test_agent_spec_model_override_field(self):
        """AgentSpec.model_override is accessible and defaults to None."""
        from qitos.core.agent_spec import AgentSpec, AgentRegistry, ContextStrategy
        from qitos.core.state import StateSchema
        from qitos.core.agent_module import AgentModule

        class _TestAgent(AgentModule):
            def init_state(self):
                return StateSchema()
            def reduce(self, decision, state=None):
                return state or {}

        agent = _TestAgent(llm=None)
        spec = AgentSpec(
            name="test_agent",
            description="Test",
            agent=agent,
            model_override="gpt-4o",
        )
        assert spec.model_override == "gpt-4o"
        assert spec.tools_override is None

    def test_agent_spec_tool_name_override_field(self):
        """AgentSpec.tool_name customizes the model-facing delegate tool name."""
        from qitos.core.agent_spec import AgentSpec, AgentRegistry
        from qitos.core.state import StateSchema
        from qitos.core.agent_module import AgentModule

        class _TestAgent(AgentModule):
            def init_state(self):
                return StateSchema()
            def reduce(self, decision, state=None):
                return state or {}

        agent = _TestAgent(llm=None)
        registry = AgentRegistry()
        registry.register(
            AgentSpec(
                name="worker",
                description="A worker",
                agent=agent,
                tool_name="research_topic",
            )
        )
        tools = registry.get_delegate_tools()
        assert tools[0].name == "research_topic"

    def test_agent_registry_get_handoff_tools(self):
        """AgentRegistry.get_handoff_tools() returns HandoffTool instances."""
        from qitos.core.agent_spec import AgentSpec, AgentRegistry
        from qitos.core.state import StateSchema
        from qitos.core.agent_module import AgentModule

        class _TestAgent(AgentModule):
            def init_state(self):
                return StateSchema()
            def reduce(self, decision, state=None):
                return state or {}

        agent = _TestAgent(llm=None)
        registry = AgentRegistry()
        registry.register(AgentSpec(name="worker", description="A worker", agent=agent))
        tools = registry.get_handoff_tools()
        assert len(tools) == 1
        assert tools[0].target_name == "worker"
        assert tools[0].spec.name == "transfer_to_worker"


# ---- Multimodal capability fallback integration ----

class TestMultimodalFallbackIntegration:
    def test_text_model_gets_dom_not_screenshot(self):
        """Text-only model observation should have DOM but no screenshot."""
        from qitos.models.profile_registry import infer_multimodal_capability

        profile = infer_multimodal_capability("meta-llama/Llama-3-8B")
        assert not profile.supports_screenshot

        pack = {
            "text": "page content",
            "screenshot": {"path": "/tmp/shot.png"},
            "dom": "<html>hello</html>",
            "ocr": [{"text": "hello"}],
        }
        adapted = profile.adapt_observation(pack)
        assert "screenshot" not in adapted
        assert "dom" in adapted

    def test_vision_model_gets_screenshot(self):
        """Vision model observation should include screenshot."""
        from qitos.models.profile_registry import infer_multimodal_capability

        profile = infer_multimodal_capability("gpt-4o-2024-05-13")
        assert profile.supports_screenshot

        pack = {
            "text": "page",
            "screenshot": {"path": "/tmp/shot.png"},
            "dom": "<html></html>",
        }
        adapted = profile.adapt_observation(pack)
        assert "screenshot" in adapted
