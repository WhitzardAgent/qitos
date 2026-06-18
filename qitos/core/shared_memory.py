"""SharedMemory: inter-agent communication channel.

Unlike Memory (agent-internal recall), SharedMemory is a shared blackboard
that multiple agents can read from and write to during a multi-agent run.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Namespace isolation
# ---------------------------------------------------------------------------


class SharedMemoryNamespace:
    """A namespaced view over a SharedMemory instance.

    All keys are prefixed with ``namespace:`` to avoid collisions between
    agents. Optionally, a namespace can be marked *read_only* so that
    sub-agents cannot modify it.

    Parameters
    ----------
    memory : SharedMemory
        The backing store.
    namespace : str
        The prefix for this namespace.
    read_only : bool
        If True, write and delete operations raise ``PermissionError``.
    """

    def __init__(
        self,
        memory: SharedMemory,
        namespace: str,
        read_only: bool = False,
    ) -> None:
        self._memory = memory
        self._namespace = namespace
        self._read_only = read_only

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def read_only(self) -> bool:
        return self._read_only

    def _prefix(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def write(self, key: str, value: Any) -> None:
        if self._read_only:
            raise PermissionError(
                f"Namespace '{self._namespace}' is read-only"
            )
        self._memory.write(self._prefix(key), value)

    def read(self, key: str) -> Optional[Any]:
        return self._memory.read(self._prefix(key))

    def delete(self, key: str) -> bool:
        if self._read_only:
            raise PermissionError(
                f"Namespace '{self._namespace}' is read-only"
            )
        return self._memory.delete(self._prefix(key))

    def list_keys(self) -> List[str]:
        prefix = f"{self._namespace}:"
        return [
            k[len(prefix):]
            for k in self._memory.list_keys()
            if k.startswith(prefix)
        ]

    def clear(self) -> None:
        if self._read_only:
            raise PermissionError(
                f"Namespace '{self._namespace}' is read-only"
            )
        for key in self.list_keys():
            self._memory.delete(self._prefix(key))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SharedMemoryManager:
    """Manages multiple SharedMemoryNamespace instances over a single store.

    Provides per-agent namespace isolation and read-only views for
    subordinate agents during multi-agent handoff.

    Parameters
    ----------
    memory : SharedMemory
        The backing store. Defaults to a new InMemorySharedMemory.
    """

    def __init__(self, memory: Optional[SharedMemory] = None) -> None:
        self._memory = memory or InMemorySharedMemory()
        self._namespaces: Dict[str, SharedMemoryNamespace] = {}
        self._access_grants: Dict[str, set] = {}  # agent_name → set of readable namespace names

    @property
    def memory(self) -> SharedMemory:
        """The underlying backing store."""
        return self._memory

    def namespace(self, name: str, read_only: bool = False) -> SharedMemoryNamespace:
        """Get or create a namespace.

        If a namespace with the same name already exists, returns the
        cached instance. If ``read_only`` differs from the existing
        namespace, returns a new read-only view (does not replace the
        cached writable instance).

        Parameters
        ----------
        name : str
            The namespace identifier (e.g. agent name).
        read_only : bool
            Whether this view is read-only.

        Returns
        -------
        SharedMemoryNamespace
        """
        if name in self._namespaces and not read_only:
            return self._namespaces[name]
        ns = SharedMemoryNamespace(self._memory, name, read_only=read_only)
        if name not in self._namespaces:
            self._namespaces[name] = ns
        return ns

    def list_namespaces(self) -> List[str]:
        """List all namespace names that have been accessed."""
        return list(self._namespaces.keys())

    def global_namespace(self) -> SharedMemoryNamespace:
        """Get the shared global namespace (not read-only)."""
        return self.namespace("__global__")

    def clear_all(self) -> None:
        """Clear all data in the backing store and reset namespace cache."""
        self._memory.clear()
        self._namespaces.clear()
        self._access_grants.clear()

    def grant_read_access(self, agent_name: str, source_namespace: str) -> None:
        """Grant an agent read access to another agent's namespace.

        Parameters
        ----------
        agent_name : str
            The agent that should receive read access.
        source_namespace : str
            The namespace the agent should be able to read.
        """
        if agent_name not in self._access_grants:
            self._access_grants[agent_name] = set()
        self._access_grants[agent_name].add(source_namespace)

    def get_accessible_namespaces(self, agent_name: str) -> List[str]:
        """Return the list of namespace names an agent can read (besides its own).

        Parameters
        ----------
        agent_name : str
            The agent whose accessible namespaces to list.

        Returns
        -------
        list[str]
            Namespace names the agent has been granted read access to.
        """
        return sorted(self._access_grants.get(agent_name, set()))

    def get_readonly_namespace(self, agent_name: str, source_namespace: str) -> Optional[SharedMemoryNamespace]:
        """Get a read-only view of another agent's namespace if access was granted.

        Parameters
        ----------
        agent_name : str
            The requesting agent.
        source_namespace : str
            The namespace to read.

        Returns
        -------
        SharedMemoryNamespace or None
            Read-only namespace view, or None if access not granted.
        """
        accessible = self._access_grants.get(agent_name, set())
        if source_namespace not in accessible:
            return None
        return self.namespace(source_namespace, read_only=True)
