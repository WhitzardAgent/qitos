"""CachedModel — transparent LLM response caching wrapper."""

from __future__ import annotations

import hashlib
import json
import pickle
from typing import Any, Dict, List, Optional

from ..models.base import Model
from .backends import CacheBackend


class CachedModel(Model):
    """Wraps any Model instance and delegates calls through a cache.

    Usage::

        from qitos.cache import CachedModel, InMemoryCache

        cache = InMemoryCache()
        llm = OpenAICompatibleModel(model="qwen-plus", ...)
        cached_llm = CachedModel(llm, cache)
        # Now pass cached_llm to your agent instead of llm

    Cache key is sha256(model_name + sorted(messages_json + kwargs_json)).
    ``__call__`` results (str) are cached as JSON; ``call_raw`` results
    (provider-native objects) are cached as pickle with a silent fallback
    on serialization failure.
    """

    def __init__(
        self,
        wrapped: Model,
        backend: CacheBackend,
        enabled: bool = True,
        ttl: Optional[float] = None,
    ):
        self._wrapped = wrapped
        self._backend = backend
        self._enabled = enabled
        self._ttl = ttl
        # Forward identity attributes
        self.model = wrapped.model
        self.system_prompt = getattr(wrapped, "system_prompt", None)
        self.temperature = getattr(wrapped, "temperature", 0.7)
        self.max_tokens = getattr(wrapped, "max_tokens", 2048)
        self.context_window = getattr(wrapped, "context_window", 128000)
        self._last_usage: Optional[Dict[str, Any]] = None
        # Cache stats
        self._hits = 0
        self._misses = 0

    def _cache_key(self, messages: List[Dict[str, Any]], kwargs: Dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "model": self._wrapped.model,
                "messages": messages,
                "kwargs": _json_safe(kwargs),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def __call__(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        if not self._enabled:
            result = self._wrapped(messages, **kwargs)
            self._set_last_usage(self._wrapped.extract_usage())
            return result

        key = self._cache_key(messages, kwargs)
        hit = self._backend.get(key)
        if hit is not None:
            self._hits += 1
            self._set_last_usage(None)
            return json.loads(hit)

        self._misses += 1
        result = self._wrapped(messages, **kwargs)
        self._set_last_usage(self._wrapped.extract_usage())
        try:
            self._backend.set(
                key, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                ttl=self._ttl,
            )
        except (TypeError, ValueError, OSError):
            pass
        return result

    def call_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        if not self._enabled:
            result = self._wrapped.call_raw(messages, **kwargs)
            self._set_last_usage(self._wrapped.extract_usage())
            return result

        key = self._cache_key(messages, kwargs)
        hit = self._backend.get(key)
        if hit is not None:
            self._hits += 1
            try:
                return pickle.loads(hit)
            except (pickle.UnpicklingError, EOFError):
                pass  # Fall through to re-fetch

        self._misses += 1
        result = self._wrapped.call_raw(messages, **kwargs)
        self._set_last_usage(self._wrapped.extract_usage())
        try:
            self._backend.set(key, pickle.dumps(result), ttl=self._ttl)
        except (pickle.PicklingError, OSError):
            pass
        return result

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        return self(messages, **kwargs)

    def supports_tool_schema_delivery(
        self, delivery: str, protocol: Any = None
    ) -> bool:
        return self._wrapped.supports_tool_schema_delivery(delivery, protocol)

    def build_tool_schema_request_options(
        self,
        tool_schema_payload: Optional[List[Dict[str, Any]]],
        *,
        protocol: Any = None,
        delivery: str = "prompt_injection",
    ) -> Dict[str, Any]:
        return self._wrapped.build_tool_schema_request_options(
            tool_schema_payload, protocol=protocol, delivery=delivery
        )

    def supports_multimodal_input(self) -> bool:
        return self._wrapped.supports_multimodal_input()

    def count_tokens(self, messages_or_text: Any) -> Optional[int]:
        return self._wrapped.count_tokens(messages_or_text)

    @property
    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _json_safe(obj: Any) -> Any:
    """Make an object JSON-serializable by converting non-serializable values."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(item) for item in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


__all__ = ["CachedModel"]
