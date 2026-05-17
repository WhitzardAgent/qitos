"""Vector-like memory implementation with pluggable embedding and storage."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from qitos.core.memory import Memory, MemoryRecord
from qitos.kit.embedding.base import Embedder
from qitos.kit.vectorstore.base import VectorMatch, VectorStore
from qitos.kit.vectorstore.memory_store import InMemoryVectorStore


class VectorMemory(Memory):
    """Memory backed by vector similarity search.

    Parameters
    ----------
    embedder : Embedder | Callable | None
        Embedding model. Accepts an ``Embedder`` instance, a plain callable
        ``(str) -> List[float]``, or None (falls back to a simple hash embedder).
    store : VectorStore | None
        Vector storage backend. If None, uses ``InMemoryVectorStore``.
    top_k : int
        Default number of results for retrieval queries.
    """

    def __init__(
        self,
        embedder: Optional[Union[Embedder, Callable[[str], List[float]]]] = None,
        store: Optional[VectorStore] = None,
        top_k: int = 5,
    ):
        if embedder is None:
            self._embedder = _HashEmbedder()
        elif isinstance(embedder, Embedder):
            self._embedder = embedder
        else:
            # Wrap plain callable as Embedder
            self._embedder = _CallableEmbedder(embedder)

        self._store = store or InMemoryVectorStore()
        self.top_k = top_k
        self._records: Dict[str, MemoryRecord] = {}

    def append(self, record: MemoryRecord) -> None:
        rid = record.metadata.get("id") or str(uuid.uuid4())
        self._records[rid] = record
        vector = self._embedder.embed(str(record.content))
        self._store.upsert(
            id=rid,
            vector=vector,
            metadata=record.metadata,
            text=str(record.content),
        )

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[MemoryRecord]:
        query = query or {}
        text = str(query.get("text", ""))
        k = int(query.get("top_k", self.top_k))
        filter_ = query.get("filter")

        if not self._records:
            return []
        if not text:
            items = list(self._records.values())[-k:]
            return items

        qv = self._embedder.embed(text)
        matches: List[VectorMatch] = self._store.query(qv, top_k=k, filter=filter_)
        results: List[MemoryRecord] = []
        for m in matches:
            rec = self._records.get(m.id)
            if rec is not None:
                results.append(rec)
        return results

    def summarize(self, max_items: int = 5) -> str:
        return "\n".join(str(r.content)[:120] for r in list(self._records.values())[-max_items:])

    def evict(self) -> int:
        return 0

    def reset(self, run_id: Optional[str] = None) -> None:
        ids = list(self._records.keys())
        self._records.clear()
        if ids:
            self._store.delete(ids)


class _HashEmbedder(Embedder):
    """Fallback hash-based embedder (not semantic, for structural testing only)."""

    @property
    def dimension(self) -> int:
        return 16

    def embed(self, text: str) -> List[float]:
        buckets = [0.0] * 16
        for i, ch in enumerate(text):
            buckets[i % 16] += (ord(ch) % 31) / 31.0
        return buckets


class _CallableEmbedder(Embedder):
    """Wraps a plain callable as an Embedder."""

    def __init__(self, fn: Callable[[str], List[float]]):
        self._fn = fn
        self._dim: Optional[int] = None

    @property
    def dimension(self) -> int:
        if self._dim is None:
            sample = self._fn("test")
            self._dim = len(sample)
        return self._dim

    def embed(self, text: str) -> List[float]:
        return self._fn(text)


__all__ = ["VectorMemory"]
