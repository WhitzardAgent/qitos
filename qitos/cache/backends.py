"""Cache backends for LLM response caching."""

from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class CacheBackend(ABC):
    """Abstract base class for cache backends."""

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        """Retrieve a cached value by key. Returns None if not found."""
        ...

    @abstractmethod
    def set(self, key: str, value: bytes, ttl: Optional[float] = None) -> None:
        """Store a value with optional TTL in seconds."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a cached value."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all cached values."""
        ...

    @abstractmethod
    def contains(self, key: str) -> bool:
        """Check if a key exists in the cache."""
        ...


class InMemoryCache(CacheBackend):
    """In-memory cache backed by a dict with optional TTL and LRU eviction."""

    def __init__(self, max_entries: int = 0, default_ttl: Optional[float] = None):
        self._store: Dict[str, tuple[bytes, Optional[float]]] = {}
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._access_order: List[str] = []

    def get(self, key: str) -> Optional[bytes]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            del self._store[key]
            try:
                self._access_order.remove(key)
            except ValueError:
                pass
            return None
        self._touch(key)
        return value

    def set(self, key: str, value: bytes, ttl: Optional[float] = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expires_at = (
            time.monotonic() + effective_ttl if effective_ttl is not None else None
        )
        if self._max_entries > 0 and key not in self._store:
            self._evict()
        self._store[key] = (value, expires_at)
        self._touch(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        try:
            self._access_order.remove(key)
        except ValueError:
            pass

    def clear(self) -> None:
        self._store.clear()
        self._access_order.clear()

    def contains(self, key: str) -> bool:
        return self.get(key) is not None

    def _touch(self, key: str) -> None:
        try:
            self._access_order.remove(key)
        except ValueError:
            pass
        self._access_order.append(key)

    def _evict(self) -> None:
        while len(self._store) >= self._max_entries and self._access_order:
            oldest = self._access_order.pop(0)
            self._store.pop(oldest, None)


class DiskCache(CacheBackend):
    """File-backed cache using a directory with one file per key."""

    def __init__(self, cache_dir: str, default_ttl: Optional[float] = None):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._default_ttl = default_ttl

    def _path(self, key: str) -> Path:
        safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / safe_key

    def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                entry = json.loads(f.read())
            value = bytes.fromhex(entry["value"])
            expires_at = entry.get("expires_at")
            if expires_at is not None and time.monotonic() > expires_at:
                path.unlink(missing_ok=True)
                return None
            return value
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, key: str, value: bytes, ttl: Optional[float] = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expires_at = (
            time.monotonic() + effective_ttl if effective_ttl is not None else None
        )
        entry = {"value": value.hex(), "expires_at": expires_at}
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(entry, f)
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)

    def clear(self) -> None:
        for path in self._cache_dir.iterdir():
            if path.is_file() and not path.suffix == ".tmp":
                try:
                    path.unlink()
                except OSError:
                    pass

    def contains(self, key: str) -> bool:
        return self.get(key) is not None


__all__ = ["CacheBackend", "InMemoryCache", "DiskCache"]
