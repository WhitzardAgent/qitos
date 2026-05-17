"""In-memory vector store — replaces VectorMemory's internal list."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .base import VectorMatch, VectorStore


class InMemoryVectorStore(VectorStore):
    """In-memory vector store using cosine similarity.

    Suitable for testing and small-scale usage. Not persistent.
    """

    def __init__(self):
        self._records: Dict[str, Dict[str, Any]] = {}

    def upsert(
        self,
        id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
    ) -> None:
        self._records[id] = {
            "id": id,
            "vector": vector,
            "metadata": metadata or {},
            "text": text,
        }

    def query(
        self,
        vector: List[float],
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorMatch]:
        if not self._records:
            return []

        scored: List[tuple[float, str]] = []
        for rid, rec in self._records.items():
            if filter and not self._matches_filter(rec["metadata"], filter):
                continue
            score = self._cosine_similarity(vector, rec["vector"])
            scored.append((score, rid))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: List[VectorMatch] = []
        for score, rid in scored[:top_k]:
            rec = self._records[rid]
            results.append(
                VectorMatch(
                    id=rid,
                    score=score,
                    metadata=rec["metadata"],
                    text=rec.get("text"),
                )
            )
        return results

    def delete(self, ids: List[str]) -> None:
        for id_ in ids:
            self._records.pop(id_, None)

    def count(self) -> int:
        return len(self._records)

    def get(self, id: str) -> Optional[VectorMatch]:
        rec = self._records.get(id)
        if rec is None:
            return None
        return VectorMatch(
            id=id,
            score=1.0,
            metadata=rec["metadata"],
            text=rec.get("text"),
        )

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @staticmethod
    def _matches_filter(metadata: Dict[str, Any], filter: Dict[str, Any]) -> bool:
        for key, value in filter.items():
            if metadata.get(key) != value:
                return False
        return True


__all__ = ["InMemoryVectorStore"]
