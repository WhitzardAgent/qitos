"""DuckDuckGo search backend — free, no API key required."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional

from .base import SearchBackend, SearchResult


class DuckDuckGoSearchBackend(SearchBackend):
    """Search backend using DuckDuckGo HTML search.

    No API key required. Uses the HTML version of DuckDuckGo for
    compatibility and rate-limit friendliness.
    """

    def __init__(self, region: str = "wt-wt", timeout: int = 10):
        self._region = region
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "duckduckgo"

    def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        try:
            return self._search_html(query, max_results)
        except Exception:
            return []

    def _search_html(self, query: str, max_results: int) -> List[SearchResult]:
        try:
            import requests
        except ImportError:
            raise ImportError("requests package is required for DuckDuckGoSearchBackend")

        url = "https://html.duckduckgo.com/html/"
        params = {"q": query, "kl": self._region}
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; QitOS/1.0)",
        }
        resp = requests.post(
            url, data=params, headers=headers, timeout=self._timeout
        )
        resp.raise_for_status()

        results: List[SearchResult] = []
        # Parse results from HTML
        result_pattern = re.compile(
            r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'<a class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        for match in result_pattern.finditer(resp.text):
            if len(results) >= max_results:
                break
            link = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
            # DuckDuckGo uses redirect URLs; extract the real URL
            real_url = urllib.parse.parse_qs(
                urllib.parse.urlparse(link).query
            ).get("uddg", [link])[0]
            results.append(
                SearchResult(
                    title=title,
                    url=real_url,
                    snippet=snippet,
                    source="duckduckgo",
                )
            )
        return results


__all__ = ["DuckDuckGoSearchBackend"]
