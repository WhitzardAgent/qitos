"""Sploitus search backend — specialized exploit/vulnerability search."""

from __future__ import annotations

from typing import Any, List, Optional

import requests

from .base import SearchBackend, SearchResult


class SploitusSearchBackend(SearchBackend):
    """Search backend using the Sploitus exploit search API.

    Sploitus is a specialized search engine for exploits, vulnerabilities,
    and PoC code. Used by PentAGI's pentester and searcher agents.

    Parameters
    ----------
    api_key : str | None
        Optional API key for Sploitus.
    base_url : str
        Sploitus API base URL.
    """

    name = "sploitus"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://sploitus.com",
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def search(
        self, query: str, max_results: int = 5, **kwargs: Any
    ) -> List[SearchResult]:
        try:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            resp = requests.get(
                f"{self._base_url}/search",
                params={
                    "q": query,
                    "limit": max_results,
                },
                headers=headers,
                timeout=kwargs.get("timeout", 15),
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            items = data if isinstance(data, list) else data.get("results", data.get("items", []))

            for item in items[:max_results]:
                if isinstance(item, dict):
                    results.append(SearchResult(
                        title=item.get("title", item.get("name", "")),
                        url=item.get("url", item.get("link", item.get("href", ""))),
                        snippet=item.get("description", item.get("snippet", "")),
                        source="sploitus",
                    ))
                elif isinstance(item, str):
                    results.append(SearchResult(
                        title="",
                        url="",
                        snippet=item,
                        source="sploitus",
                    ))

            return results[:max_results]

        except Exception:
            return []
