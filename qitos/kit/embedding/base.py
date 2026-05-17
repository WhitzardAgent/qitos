"""Embedder protocol — interface for text embedding models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class Embedder(ABC):
    """Protocol for text embedding models.

    Implementations convert text into dense vectors for similarity search.
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Embed a single text string into a vector."""

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts. Default implementation calls embed() sequentially."""
        return [self.embed(t) for t in texts]


__all__ = ["Embedder"]
