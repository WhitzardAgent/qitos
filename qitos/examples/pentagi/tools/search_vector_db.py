"""Vector DB search tools for PentAGI memory retrieval."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.tool import BaseTool, ToolSpec


class SearchGuideTool(BaseTool):
    """Search stored methodology guides in the vector database."""

    def __init__(self, memory: Optional[Any] = None):
        self._memory = memory
        super().__init__(
            ToolSpec(
                name="search_guide",
                description="Search stored methodology guides in the knowledge base. "
                "Use this to find techniques, approaches, and step-by-step guides.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query for guides",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum results (default: 5)",
                    },
                },
                required=["query"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        memory = self._memory or (runtime_context or {}).get("pentagi_memory")
        if memory is None:
            return {"status": "error", "message": "Memory not configured"}
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 5))
        try:
            records = memory.retrieve({"text": query, "top_k": top_k, "filter": {"type": "guide"}})
            items = [{"content": str(r.content)[:500], "metadata": r.metadata} for r in records]
            return {"status": "ok", "results": items, "count": len(items)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SearchAnswerTool(BaseTool):
    """Search stored Q&A pairs in the vector database."""

    def __init__(self, memory: Optional[Any] = None):
        self._memory = memory
        super().__init__(
            ToolSpec(
                name="search_answer",
                description="Search stored Q&A pairs in the knowledge base. "
                "Use this to find answers to questions that have been previously solved.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query for Q&A pairs",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum results (default: 5)",
                    },
                },
                required=["query"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        memory = self._memory or (runtime_context or {}).get("pentagi_memory")
        if memory is None:
            return {"status": "error", "message": "Memory not configured"}
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 5))
        try:
            records = memory.retrieve({"text": query, "top_k": top_k, "filter": {"type": "answer"}})
            items = [{"content": str(r.content)[:500], "metadata": r.metadata} for r in records]
            return {"status": "ok", "results": items, "count": len(items)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SearchCodeTool(BaseTool):
    """Search stored code snippets in the vector database."""

    def __init__(self, memory: Optional[Any] = None):
        self._memory = memory
        super().__init__(
            ToolSpec(
                name="search_code",
                description="Search stored code snippets in the knowledge base. "
                "Use this to find reusable code, scripts, and exploit implementations.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query for code snippets",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum results (default: 5)",
                    },
                },
                required=["query"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        memory = self._memory or (runtime_context or {}).get("pentagi_memory")
        if memory is None:
            return {"status": "error", "message": "Memory not configured"}
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 5))
        try:
            records = memory.retrieve({"text": query, "top_k": top_k, "filter": {"type": "code"}})
            items = [{"content": str(r.content)[:1000], "metadata": r.metadata} for r in records]
            return {"status": "ok", "results": items, "count": len(items)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class GraphitiSearchTool(BaseTool):
    """Search the Graphiti knowledge graph."""

    def __init__(self, graphiti_client: Optional[Any] = None):
        self._graphiti = graphiti_client
        super().__init__(
            ToolSpec(
                name="graphiti_search",
                description="Search the Graphiti knowledge graph for entity relationships "
                "and temporal context. Supports search types: recent_context, "
                "successful_tools, episode_context, entity_relationships, diverse_results.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "Search query for knowledge graph",
                    },
                    "search_type": {
                        "type": "string",
                        "description": "Type of graph search (default: recent_context)",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum results (default: 5)",
                    },
                },
                required=["query"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        client = self._graphiti or (runtime_context or {}).get("graphiti_client")
        if client is None:
            return {"status": "error", "message": "Graphiti client not configured"}
        query = str(args.get("query", ""))
        search_type = str(args.get("search_type", "recent_context"))
        top_k = int(args.get("top_k", 5))
        try:
            results = client.search(query, search_type=search_type, top_k=top_k)
            return {"status": "ok", "results": results, "count": len(results)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


__all__ = ["SearchGuideTool", "SearchAnswerTool", "SearchCodeTool", "GraphitiSearchTool"]
