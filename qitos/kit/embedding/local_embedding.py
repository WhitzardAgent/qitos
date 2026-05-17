"""Local embedding adapter using sentence-transformers."""

from __future__ import annotations

from typing import List, Optional

from .base import Embedder


class LocalEmbedder(Embedder):
    """Embedder backed by sentence-transformers (local, no API key needed).

    Parameters
    ----------
    model_name : str
        HuggingFace model name (default: ``all-MiniLM-L6-v2``).
    device : str | None
        Torch device string (e.g. ``"cuda"``, ``"cpu"``).
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: Optional[str] = None,
    ):
        self.model_name = model_name
        self._device = device
        self._model = None

    @property
    def dimension(self) -> int:
        # Lazy load to get dimension
        self._ensure_model()
        return self._model.get_sentence_embedding_dimension()

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers package is required for LocalEmbedder. "
                    "Install with: pip install sentence-transformers"
                )
            kwargs: dict = {}
            if self._device:
                kwargs["device"] = self._device
            self._model = SentenceTransformer(self.model_name, **kwargs)

    def embed(self, text: str) -> List[float]:
        self._ensure_model()
        return self._model.encode(text, convert_to_numpy=True).tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        self._ensure_model()
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        return [e.tolist() for e in embeddings]


__all__ = ["LocalEmbedder"]
