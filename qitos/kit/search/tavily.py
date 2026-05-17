"""Tavily search backend — AI-optimized search API."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base import SearchBackend, SearchResult


class TavilySearchBackend(SearchBackend):
    """Search backend using the Tavily API.

    Parameters
    ----------
    api_key : str | None
        Tavily API key. Falls back to ``TAVILY_API_KEY`` env var.
    search_depth : str
        ``"basic"`` or ``"advanced"`` (default: ``"basic"``).
    include_raw : bool
        Whether to include raw content in results.
    timeout : int
        Request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        search_depth: str = "basic",
        include_raw: bool = False,
        timeout: int = 30,
    ):
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self._search_depth = search_depth
        self._include_raw = include_raw
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "tavily"

    def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        if not self._api_key:
            return []

        try:
            import requests
        except ImportError:
            raise ImportError("requests package is required for TavilySearchBackend")

        payload: Dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": self._search_depth,
            "include_raw_content": self._include_raw,
        }

        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: List[SearchResult] = []
        for item in data.get("results", []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source="tavily",
                    metadata={
                        "score": item.get("score", 0),
                        "raw_content": item.get("raw_content", ""),
                    },
                )
            )
        return results


__all__ = ["TavilySearchBackend"]
