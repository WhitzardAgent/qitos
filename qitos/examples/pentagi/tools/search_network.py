"""Search network tools — 8 search engine backends as individual tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec
from qitos.kit.search.base import SearchBackend, SearchResult


class _SearchTool(BaseTool):
    """Base class for search network tools."""

    _backend_name: str = ""

    def __init__(
        self,
        name: str,
        description: str,
        backend: Optional[SearchBackend] = None,
    ):
        self._backend = backend
        super().__init__(
            ToolSpec(
                name=name,
                description=description,
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query text",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 8)",
                    },
                },
                required=["query"],
                permissions=ToolPermission(network=True),
            )
        )

    def _get_backend(self, runtime_context: Optional[Dict[str, Any]]) -> Optional[SearchBackend]:
        if self._backend is not None:
            return self._backend
        # Try to get backend from runtime_context
        ctx = runtime_context or {}
        backends = ctx.get("search_backends", {})
        return backends.get(self._backend_name)

    def _format_results(self, results: List[SearchResult]) -> Dict[str, Any]:
        items = []
        for r in results:
            items.append({
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "source": r.source,
            })
        return {"status": "ok", "results": items, "count": len(items)}


class GoogleSearchTool(_SearchTool):
    _backend_name = "google_cse"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="google_search",
            description="Search the web using Google Custom Search Engine. "
            "Good for general web searches and finding documentation.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "Google CSE backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class DuckDuckGoSearchTool(_SearchTool):
    _backend_name = "duckduckgo"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="duckduckgo_search",
            description="Search the web using DuckDuckGo. Free, no API key required. "
            "Good for general web searches.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "DuckDuckGo backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class TavilySearchTool(_SearchTool):
    _backend_name = "tavily"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="tavily_search",
            description="Search the web using Tavily AI-powered search. "
            "Provides high-quality, AI-optimized search results.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "Tavily backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SearXNGSearchTool(_SearchTool):
    _backend_name = "searxng"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="searxng_search",
            description="Search using a self-hosted SearXNG instance. "
            "Aggregates results from multiple search engines.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "SearXNG backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SploitusSearchTool(_SearchTool):
    _backend_name = "sploitus"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="sploitus_search",
            description="Search for exploits and vulnerabilities using Sploitus. "
            "Specialized for security research — finds CVEs, exploit code, and PoCs.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "Sploitus backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SearchInMemoryTool(BaseTool):
    """Search previously stored results in PentAGI memory."""

    def __init__(self, memory: Optional[Any] = None):
        self._memory = memory
        super().__init__(
            ToolSpec(
                name="search_in_memory",
                description="Search previously stored results in the agent's long-term memory. "
                "Use this to find information from previous tasks or runs.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query text",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 5)",
                    },
                },
                required=["query"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        memory = self._memory
        if memory is None:
            runtime_context = runtime_context or {}
            memory = runtime_context.get("pentagi_memory")
        if memory is None:
            return {"status": "error", "message": "Memory not configured"}

        query_text = str(args.get("query", ""))
        top_k = int(args.get("top_k", 5))

        try:
            records = memory.retrieve({"text": query_text, "top_k": top_k})
            items = []
            for r in records:
                items.append({
                    "content": str(r.content)[:500],
                    "role": r.role,
                    "step_id": r.step_id,
                    "metadata": r.metadata,
                })
            return {"status": "ok", "results": items, "count": len(items)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class TraversaalSearchTool(_SearchTool):
    _backend_name = "traversaal"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="traversaal_search",
            description="Search the web using Traversaal Ares AI search. "
            "Good for AI-optimized results with real-time information.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "Traversaal backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


class PerplexitySearchTool(_SearchTool):
    _backend_name = "perplexity"

    def __init__(self, backend: Optional[SearchBackend] = None):
        super().__init__(
            name="perplexity_search",
            description="Search using Perplexity AI. Provides AI-synthesized answers "
            "with source citations. Good for complex technical questions.",
            backend=backend,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backend = self._get_backend(runtime_context)
        if backend is None:
            return {"status": "error", "message": "Perplexity backend not configured"}
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        try:
            results = backend.search(query, max_results)
            return self._format_results(results)
        except Exception as e:
            return {"status": "error", "message": str(e)}


__all__ = [
    "GoogleSearchTool",
    "DuckDuckGoSearchTool",
    "TavilySearchTool",
    "SearXNGSearchTool",
    "SploitusSearchTool",
    "TraversaalSearchTool",
    "PerplexitySearchTool",
    "SearchInMemoryTool",
]
