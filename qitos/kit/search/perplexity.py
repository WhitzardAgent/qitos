"""Perplexity search backend — uses the Perplexity API."""

from __future__ import annotations

from typing import Any, List, Optional

import requests

from .base import SearchBackend, SearchResult


class PerplexitySearchBackend(SearchBackend):
    """Search backend using the Perplexity API.

    Parameters
    ----------
    api_key : str
        Perplexity API key.
    base_url : str
        API base URL.
    model : str
        Model to use for search-augmented generation.
    """

    name = "perplexity"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.perplexity.ai",
        model: str = "sonar",
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    def search(
        self, query: str, max_results: int = 5, **kwargs: Any
    ) -> List[SearchResult]:
        try:
            resp = requests.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Be precise and concise. Provide factual information "
                                "with source URLs when available."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": kwargs.get("max_tokens", 1024),
                },
                timeout=kwargs.get("timeout", 30),
            )
            resp.raise_for_status()
            data = resp.json()

            # Perplexity returns a chat completion with citations
            content = ""
            citations = []
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                citations = choices[0].get("message", {}).get("citations", [])

            results = []
            # If we have citations, create results from them
            for i, url in enumerate(citations[:max_results]):
                results.append(SearchResult(
                    title=f"Source {i + 1}",
                    url=url,
                    snippet=content[:300] if i == 0 else "",
                    source="perplexity",
                ))

            # If no citations but we have content, return as single result
            if not results and content:
                results.append(SearchResult(
                    title="Perplexity Answer",
                    url="",
                    snippet=content[:500],
                    source="perplexity",
                ))

            return results[:max_results]

        except Exception:
            return []
