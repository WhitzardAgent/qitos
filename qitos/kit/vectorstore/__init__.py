"""Vector store protocols and implementations for persistent vector storage."""

from .base import VectorStore, VectorMatch
from .memory_store import InMemoryVectorStore
from .pgvector_store import PgVectorStore

__all__ = ["VectorStore", "VectorMatch", "InMemoryVectorStore", "PgVectorStore"]
