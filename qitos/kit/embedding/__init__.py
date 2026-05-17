"""Embedding protocols and implementations for vector-based memory."""

from .base import Embedder
from .openai_embedding import OpenAIEmbedder
from .local_embedding import LocalEmbedder

__all__ = ["Embedder", "OpenAIEmbedder", "LocalEmbedder"]
