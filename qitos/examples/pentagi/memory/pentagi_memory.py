"""PentAGIMemory — unified memory facade routing to appropriate backends."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.memory import Memory, MemoryRecord
from qitos.kit.memory.vector_memory import VectorMemory
from qitos.kit.embedding.base import Embedder
from qitos.kit.vectorstore.base import VectorStore


class PentAGIMemory(Memory):
    """Unified memory facade for PentAGI system.

    Routes store/search operations to the appropriate backend
    based on content type (guide, answer, code, finding, etc.).

    Backends (in priority order):
    1. pgvector — if pgvector_connection is provided
    2. InMemoryVectorStore — default, fine for testing

    Graphiti can be enabled separately for knowledge graph operations.
    """

    def __init__(
        self,
        embedder: Optional[Embedder] = None,
        vector_store: Optional[VectorStore] = None,
        graphiti_client: Optional[Any] = None,
        pgvector_connection: Optional[str] = None,
    ):
        if vector_store is None and pgvector_connection:
            vector_store = self._create_pgvector_store(pgvector_connection)
        self._vector_memory = VectorMemory(
            embedder=embedder,
            store=vector_store,
        )
        self._graphiti = graphiti_client

    @staticmethod
    def _create_pgvector_store(connection_string: str) -> VectorStore:
        """Create a PgVectorStore from a connection string."""
        try:
            from qitos.kit.vectorstore.pgvector_store import PgVectorStore
            return PgVectorStore(connection_string=connection_string)
        except ImportError:
            raise ImportError(
                "pgvector support requires psycopg2. "
                "Install with: pip install psycopg2-binary"
            )

    def append(self, record: MemoryRecord) -> None:
        """Store a record. Routes based on metadata type."""
        self._vector_memory.append(record)

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[MemoryRecord]:
        """Retrieve records by semantic similarity, optionally filtered by type."""
        return self._vector_memory.retrieve(query, state, observation)

    def summarize(self, max_items: int = 5) -> str:
        return self._vector_memory.summarize(max_items)

    def evict(self) -> int:
        return self._vector_memory.evict()

    def reset(self, run_id: Optional[str] = None) -> None:
        self._vector_memory.reset(run_id)

    @property
    def vector_memory(self) -> VectorMemory:
        """Access the underlying VectorMemory for direct operations."""
        return self._vector_memory

    @property
    def graphiti(self) -> Optional[Any]:
        """Access the Graphiti client if configured."""
        return self._graphiti


__all__ = ["PentAGIMemory"]
