"""Google Custom Search Engine backend."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base import SearchBackend, SearchResult


class GoogleCSESearchBackend(SearchBackend):
    """Search backend using Google Custom Search API.

    Parameters
    ----------
    api_key : str | None
        Google API key. Falls back to ``GOOGLE_API_KEY`` env var.
    cx : str | None
        Custom Search Engine ID. Falls back to ``GOOGLE_CX`` env var.
    timeout : int
        Request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        cx: Optional[str] = None,
        timeout: int = 10,
    ):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._cx = cx or os.environ.get("GOOGLE_CX", "")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "google_cse"

    def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        if not self._api_key or not self._cx:
            return []

        try:
            import requests
        except ImportError:
            raise ImportError("requests package is required for GoogleCSESearchBackend")

        params: Dict[str, Any] = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "num": min(max_results, 10),
        }

        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results: List[SearchResult] = []
        for item in data.get("items", []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    source="google_cse",
                    metadata={
                        "formatted_url": item.get("formattedUrl", ""),
                        "mime": item.get("mime", ""),
                    },
                )
            )
        return results


__all__ = ["GoogleCSESearchBackend"]
