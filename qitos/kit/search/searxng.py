"""SearXNG search backend — self-hosted meta search engine."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import SearchBackend, SearchResult


class SearXNGSearchBackend(SearchBackend):
    """Search backend using a SearXNG instance.

    Parameters
    ----------
    base_url : str
        Base URL of the SearXNG instance (e.g. ``"http://localhost:8080"``).
    categories : str | None
        SearXNG search categories (e.g. ``"general"``, ``"it"``).
    language : str
        Search language (default: ``"auto"``).
    timeout : int
        Request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        categories: Optional[str] = None,
        language: str = "auto",
        timeout: int = 10,
    ):
        self._base_url = base_url.rstrip("/")
        self._categories = categories
        self._language = language
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "searxng"

    def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        try:
            import requests
        except ImportError:
            raise ImportError("requests package is required for SearXNGSearchBackend")

        params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "pageno": 1,
        }
        if self._categories:
            params["categories"] = self._categories
        if self._language:
            params["language"] = self._language

        try:
            resp = requests.get(
                f"{self._base_url}/search",
                params=params,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: List[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source="searxng",
                    metadata={
                        "engine": item.get("engine", ""),
                        "score": item.get("score", 0),
                    },
                )
            )
        return results


__all__ = ["SearXNGSearchBackend"]
