"""Tests for YAML Agent Configuration."""

import os
import tempfile

import pytest

from qitos.config import (
    AgentConfig,
    ModelConfig,
    DatasetItem,
    load_agent_config,
    resolve_env_vars,
    build_run_spec,
    build_tool_registry,
)


# --- resolve_env_vars tests ---


class TestResolveEnvVars:
    def test_resolves_env_var(self):
        os.environ["TEST_QITOS_KEY"] = "secret123"
        result = resolve_env_vars("key=${TEST_QITOS_KEY}")
        assert result == "key=secret123"
        del os.environ["TEST_QITOS_KEY"]

    def test_missing_env_var_empty(self):
        result = resolve_env_vars("key=${NONEXISTENT_VAR_XYZ}")
        assert result == "key="

    def test_no_env_vars(self):
        assert resolve_env_vars("plain text") == "plain text"

    def test_dict_recursive(self):
        os.environ["TEST_QITOS_HOST"] = "api.example.com"
        result = resolve_env_vars({"url": "https://${TEST_QITOS_HOST}/v1"})
        assert result["url"] == "https://api.example.com/v1"
        del os.environ["TEST_QITOS_HOST"]

    def test_list_recursive(self):
        os.environ["TEST_QITOS_VAL"] = "resolved"
        result = resolve_env_vars(["${TEST_QITOS_VAL}", "static"])
        assert result[0] == "resolved"
        assert result[1] == "static"
        del os.environ["TEST_QITOS_VAL"]

    def test_non_string_unchanged(self):
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(None) is None


# --- load_agent_config tests ---


class TestLoadAgentConfig:
    def test_load_basic_yaml(self):
        yaml_content = """
name: test_agent
max_steps: 5
model:
  provider: openai
  model: gpt-4o
  temperature: 0.2
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            config = load_agent_config(f.name)

        assert config.name == "test_agent"
        assert config.max_steps == 5
        assert config.model.provider == "openai"
        assert config.model.model == "gpt-4o"
        assert config.model.temperature == 0.2
        os.unlink(f.name)

    def test_load_with_dataset(self):
        yaml_content = """
name: math_agent
max_steps: 10
model:
  provider: openai
  model: gpt-4
dataset:
  - task: "2 + 3"
    expected: "5"
  - task: "10 - 4"
    expected: "6"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            config = load_agent_config(f.name)

        assert len(config.dataset) == 2
        assert config.dataset[0].task == "2 + 3"
        assert config.dataset[0].expected == "5"
        os.unlink(f.name)

    def test_load_with_env_vars(self):
        os.environ["TEST_QITOS_API"] = "sk-test-key"
        yaml_content = """
name: env_agent
model:
  provider: openai
  api_key: ${TEST_QITOS_API}
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            config = load_agent_config(f.name)

        assert config.model.api_key == "sk-test-key"
        del os.environ["TEST_QITOS_API"]
        os.unlink(f.name)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_agent_config("/nonexistent/path/config.yaml")

    def test_defaults(self):
        yaml_content = """
name: minimal
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            config = load_agent_config(f.name)

        assert config.max_steps == 10
        assert config.model.provider == "openai"
        assert config.model.temperature == 0.7
        assert config.dataset == []
        assert config.tools == []
        os.unlink(f.name)

    def test_string_dataset_items(self):
        yaml_content = """
dataset:
  - "task one"
  - "task two"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            config = load_agent_config(f.name)

        assert len(config.dataset) == 2
        assert config.dataset[0].task == "task one"
        os.unlink(f.name)


# --- build_run_spec tests ---


class TestBuildRunSpec:
    def test_basic(self):
        config = AgentConfig(
            name="test", model=ModelConfig(model="gpt-4o"), seed=42
        )
        spec = build_run_spec(config)
        assert spec.seed == 42


# --- build_tool_registry tests ---


class TestBuildToolRegistry:
    def test_empty_tools(self):
        config = AgentConfig(name="test", tools=[])
        registry = build_tool_registry(config)
        assert len(registry.list_tools()) == 0

    def test_invalid_tool_path(self):
        config = AgentConfig(name="test", tools=["nonexistent.module.func"])
        with pytest.raises(ImportError):
            build_tool_registry(config)

    def test_malformed_tool_path(self):
        config = AgentConfig(name="test", tools=["no_dot_path"])
        with pytest.raises(ImportError, match="Invalid tool path"):
            build_tool_registry(config)


# --- AgentConfig.to_dict tests ---


class TestAgentConfigToDict:
    def test_round_trip(self):
        config = AgentConfig(
            name="test",
            max_steps=5,
            model=ModelConfig(provider="openai", model="gpt-4"),
            dataset=[DatasetItem(task="hello", expected="world")],
            tools=["qitos.kit.search.web_search"],
            protocol="react_text_v1",
            seed=42,
        )
        d = config.to_dict()
        assert d["name"] == "test"
        assert d["max_steps"] == 5
        assert d["model"]["provider"] == "openai"
        assert len(d["dataset"]) == 1
        assert d["dataset"][0]["task"] == "hello"
