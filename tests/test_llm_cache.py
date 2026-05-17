"""Tests for LLM Cache: backends, CachedModel, and Engine integration."""

import json
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from qitos import AgentModule, Decision, Action, Engine, StateSchema, ToolRegistry, tool
from qitos.cache import CacheBackend, InMemoryCache, DiskCache, CachedModel
from qitos.engine import RuntimeBudget


# --- Fixtures ---


@dataclass
class DemoState(StateSchema):
    logs: list[str] = field(default_factory=list)


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def __init__(self, answer: str = "42", with_llm: bool = False):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        self._answer = answer
        super().__init__(tool_registry=registry)
        if with_llm:
            self.llm = _StubModel(answer=answer)

    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=3)

    def decide(self, state: DemoState, observation: dict[str, Any]) -> Decision[Action]:
        if state.current_step == 0:
            return Decision.act(
                actions=[Action(name="add", args={"a": 1, "b": 2})],
                rationale="use tool",
            )
        return Decision.final(self._answer)

    def reduce(
        self,
        state: DemoState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> DemoState:
        return state


class _StubModel:
    """Sync model that counts calls and returns a fixed answer."""

    def __init__(self, answer: str = "done", model: str = "stub"):
        self.model = model
        self.answer = answer
        self._last_usage = None
        self.call_count = 0
        self.call_raw_count = 0

    def __call__(self, messages, **kwargs):
        self.call_count += 1
        return f"Final Answer: {self.answer}"

    def call_raw(self, messages, **kwargs):
        self.call_raw_count += 1
        return self(messages, **kwargs)

    def extract_usage(self, response=None):
        return self._last_usage


# --- InMemoryCache tests ---


class TestInMemoryCache:
    def test_get_set(self):
        cache = InMemoryCache()
        cache.set("key1", b"value1")
        assert cache.get("key1") == b"value1"

    def test_get_missing(self):
        cache = InMemoryCache()
        assert cache.get("nonexistent") is None

    def test_delete(self):
        cache = InMemoryCache()
        cache.set("key1", b"value1")
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_clear(self):
        cache = InMemoryCache()
        cache.set("key1", b"value1")
        cache.set("key2", b"value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_contains(self):
        cache = InMemoryCache()
        cache.set("key1", b"value1")
        assert cache.contains("key1")
        assert not cache.contains("key2")

    def test_ttl_expiry(self):
        cache = InMemoryCache()
        cache.set("key1", b"value1", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_max_entries_lru(self):
        cache = InMemoryCache(max_entries=2)
        cache.set("a", b"1")
        cache.set("b", b"2")
        cache.set("c", b"3")  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == b"2"
        assert cache.get("c") == b"3"

    def test_overwrite(self):
        cache = InMemoryCache()
        cache.set("key1", b"old")
        cache.set("key1", b"new")
        assert cache.get("key1") == b"new"


# --- DiskCache tests ---


class TestDiskCache:
    def test_get_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            cache.set("key1", b"value1")
            assert cache.get("key1") == b"value1"

    def test_get_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            assert cache.get("nonexistent") is None

    def test_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            cache.set("key1", b"value1")
            cache.delete("key1")
            assert cache.get("key1") is None

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            cache.set("key1", b"value1")
            cache.set("key2", b"value2")
            cache.clear()
            assert cache.get("key1") is None
            assert cache.get("key2") is None

    def test_contains(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            cache.set("key1", b"value1")
            assert cache.contains("key1")

    def test_ttl_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(tmpdir)
            cache.set("key1", b"value1", ttl=0.01)
            time.sleep(0.02)
            assert cache.get("key1") is None

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache1 = DiskCache(tmpdir)
            cache1.set("key1", b"value1")
            # New instance pointing to same dir
            cache2 = DiskCache(tmpdir)
            assert cache2.get("key1") == b"value1"


# --- CachedModel tests ---


class TestCachedModel:
    def test_cache_hit_call(self):
        model = _StubModel(answer="hello")
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        messages = [{"role": "user", "content": "test"}]
        result1 = cached(messages)
        result2 = cached(messages)

        assert result1 == "Final Answer: hello"
        assert result2 == "Final Answer: hello"
        assert model.call_count == 1  # only called once; second was cache hit
        assert cached.stats["hits"] == 1
        assert cached.stats["misses"] == 1

    def test_cache_miss_different_messages(self):
        model = _StubModel(answer="hello")
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        cached([{"role": "user", "content": "msg1"}])
        cached([{"role": "user", "content": "msg2"}])

        assert model.call_count == 2
        assert cached.stats["misses"] == 2

    def test_cache_key_deterministic(self):
        model = _StubModel()
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        messages = [{"role": "user", "content": "test"}]
        key1 = cached._cache_key(messages, {})
        key2 = cached._cache_key(messages, {})
        assert key1 == key2

    def test_enabled_false_bypasses(self):
        model = _StubModel(answer="hello")
        cache = InMemoryCache()
        cached = CachedModel(model, cache, enabled=False)

        messages = [{"role": "user", "content": "test"}]
        cached(messages)
        cached(messages)

        assert model.call_count == 2  # both calls went through
        assert cached.stats["hits"] == 0

    def test_forwards_attributes(self):
        model = _StubModel()
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        assert cached.model == "stub"
        assert cached.temperature == 0.7
        assert cached.max_tokens == 2048

    def test_call_raw_cache_hit(self):
        model = _StubModel(answer="raw_result")
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        messages = [{"role": "user", "content": "test"}]
        cached.call_raw(messages)
        cached.call_raw(messages)

        assert model.call_raw_count == 1  # second was cache hit

    def test_stats(self):
        model = _StubModel()
        cache = InMemoryCache()
        cached = CachedModel(model, cache)

        messages = [{"role": "user", "content": "test"}]
        cached(messages)  # miss
        cached(messages)  # hit

        assert cached.stats == {"hits": 1, "misses": 1}


# --- Engine integration ---


class TestEngineCacheIntegration:
    def test_engine_auto_wraps_model(self):
        agent = DemoAgent(answer="cached_result", with_llm=True)
        cache = InMemoryCache()
        engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=5), cache_backend=cache)

        # The agent's llm should have been wrapped
        from qitos.cache import CachedModel

        assert isinstance(engine.agent.llm, CachedModel)

    def test_engine_no_cache_when_none(self):
        agent = DemoAgent()
        engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=5))

        assert engine.cache_backend is None

    def test_engine_with_cache_produces_result(self):
        agent = DemoAgent(answer="from_cache")
        cache = InMemoryCache()
        engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=5), cache_backend=cache)
        result = engine.run("test task")
        assert result.state.final_result == "from_cache"
