"""Tests for SharedMemory implementations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from qitos.core.shared_memory import (
    FileSharedMemory,
    InMemorySharedMemory,
    SharedMemory,
)


class TestInMemorySharedMemory:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            SharedMemory()

    def test_write_and_read(self):
        mem = InMemorySharedMemory()
        mem.write("key1", "value1")
        assert mem.read("key1") == "value1"

    def test_read_missing_key(self):
        mem = InMemorySharedMemory()
        assert mem.read("nonexistent") is None

    def test_delete(self):
        mem = InMemorySharedMemory()
        mem.write("key1", "value1")
        assert mem.delete("key1") is True
        assert mem.read("key1") is None
        assert mem.delete("key1") is False

    def test_list_keys(self):
        mem = InMemorySharedMemory()
        mem.write("a", 1)
        mem.write("b", 2)
        assert set(mem.list_keys()) == {"a", "b"}

    def test_clear(self):
        mem = InMemorySharedMemory()
        mem.write("a", 1)
        mem.write("b", 2)
        mem.clear()
        assert mem.list_keys() == []

    def test_overwrite(self):
        mem = InMemorySharedMemory()
        mem.write("key", "old")
        mem.write("key", "new")
        assert mem.read("key") == "new"

    def test_complex_values(self):
        mem = InMemorySharedMemory()
        mem.write("data", {"nested": [1, 2, 3]})
        assert mem.read("data") == {"nested": [1, 2, 3]}


class TestFileSharedMemory:
    def test_write_and_read(self, tmp_path):
        mem = FileSharedMemory(tmp_path / "shared.json")
        mem.write("key1", "value1")
        assert mem.read("key1") == "value1"

    def test_persistence(self, tmp_path):
        path = tmp_path / "shared.json"
        mem1 = FileSharedMemory(path)
        mem1.write("key1", "value1")

        # New instance reading same file
        mem2 = FileSharedMemory(path)
        assert mem2.read("key1") == "value1"

    def test_delete(self, tmp_path):
        mem = FileSharedMemory(tmp_path / "shared.json")
        mem.write("key1", "value1")
        assert mem.delete("key1") is True
        assert mem.read("key1") is None

    def test_list_keys(self, tmp_path):
        mem = FileSharedMemory(tmp_path / "shared.json")
        mem.write("a", 1)
        mem.write("b", 2)
        assert set(mem.list_keys()) == {"a", "b"}

    def test_clear(self, tmp_path):
        mem = FileSharedMemory(tmp_path / "shared.json")
        mem.write("a", 1)
        mem.clear()
        assert mem.list_keys() == []

    def test_json_format(self, tmp_path):
        path = tmp_path / "shared.json"
        mem = FileSharedMemory(path)
        mem.write("key", "value")
        data = json.loads(path.read_text())
        assert data["key"] == "value"

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "subdir" / "deep" / "shared.json"
        mem = FileSharedMemory(path)
        mem.write("key", "value")
        assert mem.read("key") == "value"


class TestSharedMemoryInAgentSpec:
    def test_shared_memory_on_agent_spec(self):
        from dataclasses import dataclass, field
        from typing import Any
        from qitos import Action, AgentModule, AgentSpec, StateSchema, ToolRegistry
        from qitos.core.shared_memory import InMemorySharedMemory

        @dataclass
        class DummyState(StateSchema):
            scratchpad: list[str] = field(default_factory=list)

        class DummyAgent(AgentModule[DummyState, dict[str, Any], Action]):
            def __init__(self, name: str = "agent"):
                registry = ToolRegistry()
                super().__init__(tool_registry=registry)
                self.name = name

            def init_state(self, task: str, **kwargs: Any) -> DummyState:
                return DummyState(task=task, max_steps=3)

            def reduce(self, state, observation, decision):
                return state

        mem = InMemorySharedMemory()
        mem.write("shared_key", "shared_value")

        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            shared_memory=mem,
        )
        assert spec.shared_memory is not None
        assert spec.shared_memory.read("shared_key") == "shared_value"

    def test_shared_memory_in_runtime_context(self):
        from qitos import ToolRegistry
        from qitos.core.shared_memory import InMemorySharedMemory
        from qitos.engine.action_executor import ActionExecutor

        mem = InMemorySharedMemory()
        mem.write("test_key", "test_value")

        executor = ActionExecutor(tool_registry=ToolRegistry(), shared_memory=mem)
        ctx = executor._build_runtime_context("some_tool", env=None, state=None)
        assert ctx["shared_memory"] is mem
        assert ctx["shared_memory"].read("test_key") == "test_value"
