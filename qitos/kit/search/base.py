"""SearchBackend protocol — interface for search engine backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SearchBackend(ABC):
    """Protocol for search engine backends.

    Implementations provide a ``search`` method that returns a list
    of ``SearchResult`` objects for a given query.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name (e.g. 'duckduckgo', 'google_cse')."""

    @abstractmethod
    def search(
        self, query: str, max_results: int = 8
    ) -> List[SearchResult]:
        """Search for the given query and return results."""


__all__ = ["SearchBackend", "SearchResult"]
