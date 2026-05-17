"""OpenAI embedding adapter."""

from __future__ import annotations

import os
from typing import List, Optional

from .base import Embedder


class OpenAIEmbedder(Embedder):
    """Embedder backed by the OpenAI Embeddings API.

    Parameters
    ----------
    model : str
        Model name (default: ``text-embedding-3-small``).
    api_key : str | None
        OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
    base_url : str | None
        Custom API base URL (for compatible endpoints).
    dimensions : int | None
        Override output dimensions (only supported by ``text-embedding-3-*`` models).
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dimensions: Optional[int] = None,
    ):
        self.model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        self._dimensions = dimensions
        self._client = None

    @property
    def dimension(self) -> int:
        if self._dimensions:
            return self._dimensions
        defaults = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return defaults.get(self.model, 1536)

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package is required for OpenAIEmbedder. "
                    "Install with: pip install openai"
                )
            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def embed(self, text: str) -> List[float]:
        client = self._get_client()
        kwargs: dict = {"model": self.model, "input": text}
        if self._dimensions:
            kwargs["dimensions"] = self._dimensions
        response = client.embeddings.create(**kwargs)
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        kwargs: dict = {"model": self.model, "input": texts}
        if self._dimensions:
            kwargs["dimensions"] = self._dimensions
        response = client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]


__all__ = ["OpenAIEmbedder"]
