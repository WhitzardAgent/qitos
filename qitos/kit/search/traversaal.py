"""Traversaal search backend — uses the Traversaal Ares API."""

from __future__ import annotations

from typing import Any, List, Optional

import requests

from .base import SearchBackend, SearchResult


class TraversaalSearchBackend(SearchBackend):
    """Search backend using the Traversaal Ares API.

    Parameters
    ----------
    api_key : str
        Traversaal API key.
    base_url : str
        API base URL.
    """

    name = "traversaal"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.traversaal.ai",
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def search(
        self, query: str, max_results: int = 5, **kwargs: Any
    ) -> List[SearchResult]:
        try:
            resp = requests.post(
                f"{self._base_url}/v1/search",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "num_results": max_results,
                },
                timeout=kwargs.get("timeout", 10),
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("data", {}).get("response", []):
                if isinstance(item, dict):
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", item.get("link", "")),
                        snippet=item.get("description", item.get("snippet", "")),
                        source="traversaal",
                    ))
                elif isinstance(item, str):
                    results.append(SearchResult(
                        title="",
                        url="",
                        snippet=item,
                        source="traversaal",
                    ))
            return results[:max_results]

        except Exception:
            return []
