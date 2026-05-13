"""SharedMemory: inter-agent communication channel.

Unlike Memory (agent-internal recall), SharedMemory is a shared blackboard
that multiple agents can read from and write to during a multi-agent run.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class SharedMemory(ABC):
    """Abstract interface for inter-agent shared state."""

    @abstractmethod
    def write(self, key: str, value: Any) -> None:
        """Write a value to the shared store."""

    @abstractmethod
    def read(self, key: str) -> Optional[Any]:
        """Read a value from the shared store. Returns None if not found."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if the key existed."""

    @abstractmethod
    def list_keys(self) -> List[str]:
        """List all keys in the store."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all entries."""


class InMemorySharedMemory(SharedMemory):
    """Thread-safe in-memory shared memory."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def write(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def read(self, key: str) -> Optional[Any]:
        with self._lock:
            return self._data.get(key)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def list_keys(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class FileSharedMemory(SharedMemory):
    """File-backed shared memory. Writes to a JSON file on disk.

    Useful for persistence across agent runs and for debugging.
    Thread-safe via file locking.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _read_all(self) -> Dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_all(self, data: Dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def write(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._read_all()
            data[key] = value
            self._write_all(data)

    def read(self, key: str) -> Optional[Any]:
        with self._lock:
            data = self._read_all()
            return data.get(key)

    def delete(self, key: str) -> bool:
        with self._lock:
            data = self._read_all()
            if key in data:
                del data[key]
                self._write_all(data)
                return True
            return False

    def list_keys(self) -> List[str]:
        with self._lock:
            data = self._read_all()
            return list(data.keys())

    def clear(self) -> None:
        with self._lock:
            self._write_all({})
