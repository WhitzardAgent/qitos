"""VectorStore protocol — interface for persistent vector storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VectorMatch:
    """A single match from a vector similarity query."""

    id: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    text: Optional[str] = None


class VectorStore(ABC):
    """Protocol for vector storage backends.

    Implementations provide persistent (or in-memory) storage of vectors
    with metadata, supporting upsert, similarity query, and delete operations.
    """

    @abstractmethod
    def upsert(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
    ) -> None:
        """Insert or update a vector record."""

    @abstractmethod
    def query(
        self,
        vector: List[float],
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorMatch]:
        """Query for similar vectors, optionally filtered by metadata."""

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """Delete records by ID."""

    @abstractmethod
    def count(self) -> int:
        """Return the number of stored records."""

    def get(self, id: str) -> Optional[VectorMatch]:
        """Retrieve a single record by ID. Default: returns None."""
        return None


__all__ = ["VectorStore", "VectorMatch"]
