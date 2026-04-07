"""Atomic browser tools backed by TextWebEnv web_browser ops."""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec


class _WebBrowserTool(BaseTool):
    required_ops = ["web_browser"]

    def _ops(self, runtime_context: Optional[Dict[str, Any]]) -> Any:
        runtime_context = runtime_context or {}
        ops = runtime_context.get("ops", {})
        browser = ops.get("web_browser")
        if browser is None:
            raise ValueError("Missing required ops group: web_browser")
        return browser


class WebSearch(_WebBrowserTool):
    """Search the web through the active text-browser environment.

    Use this tool to find candidate pages before opening one into the browser's
    current reading state.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="web_search",
                description="Search the web and return top text results",
                parameters={
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                required=["query"],
                permissions=ToolPermission(network=True),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search the web and return top readable results through the browser env.

        :param query: Search query text.
        :param max_results: Maximum number of search results to return.
        :param runtime_context: Optional runtime ops injected by the engine.

        The browser env keeps any navigation state needed for later page visits.
        """
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        return self._ops(runtime_context).search(query=query, max_results=max_results)


class VisitURL(_WebBrowserTool):
    """Open a URL and load readable text into the browser state.

    Use this tool after selecting a result to make its content available for
    paging, searching, and summarization.
    """

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="visit_url",
                description="Visit URL and load readable text into browser state",
                parameters={
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                required=["url"],
                permissions=ToolPermission(network=True),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Visit a URL and load its readable text into the browser state.

        :param url: Absolute URL to open.
        :param max_chars: Maximum number of characters to keep in the loaded page.
        :param runtime_context: Optional runtime ops injected by the engine.

        The returned payload reflects the browser env's current page state.
        """
        url = str(args.get("url", ""))
        max_chars = int(args.get("max_chars", 30000))
        return self._ops(runtime_context).visit(url=url, max_chars=max_chars)


class PageDown(_WebBrowserTool):
    """Advance the browser's current text window downward by a number of lines."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="page_down",
                description="Move text page cursor down",
                parameters={"lines": {"type": "integer"}},
                required=[],
                permissions=ToolPermission(),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Scroll the current text page downward.

        :param lines: Number of lines to move down.
        :param runtime_context: Optional runtime ops injected by the engine.
        """
        lines = int(args.get("lines", 40))
        return self._ops(runtime_context).page_down(lines=lines)


class PageUp(_WebBrowserTool):
    """Move the browser's current text window upward by a number of lines."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="page_up",
                description="Move text page cursor up",
                parameters={"lines": {"type": "integer"}},
                required=[],
                permissions=ToolPermission(),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Scroll the current text page upward.

        :param lines: Number of lines to move up.
        :param runtime_context: Optional runtime ops injected by the engine.
        """
        lines = int(args.get("lines", 40))
        return self._ops(runtime_context).page_up(lines=lines)


class FindInPage(_WebBrowserTool):
    """Find the first occurrence of a keyword in the current page and move to it."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="find_in_page",
                description="Find keyword in current page and move cursor",
                parameters={"keyword": {"type": "string"}},
                required=["keyword"],
                permissions=ToolPermission(),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Find a keyword in the current page and jump to its first occurrence.

        :param keyword: Text to search for in the current page.
        :param runtime_context: Optional runtime ops injected by the engine.
        """
        keyword = str(args.get("keyword", ""))
        return self._ops(runtime_context).find(keyword=keyword)


class FindNext(_WebBrowserTool):
    """Jump to the next occurrence of the most recent in-page search keyword."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="find_next",
                description="Find next match for previous keyword on page",
                parameters={},
                required=[],
                permissions=ToolPermission(),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Jump to the next occurrence of the most recent page search.

        :param runtime_context: Optional runtime ops injected by the engine.
        """
        _ = args
        return self._ops(runtime_context).find_next()


class ArchiveSearch(_WebBrowserTool):
    """Search archived web snapshots through the active text-browser environment."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="archive_search",
                description="Search the web archive for historical pages",
                parameters={
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                required=["query"],
                permissions=ToolPermission(network=True),
                required_ops=self.required_ops,
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search archived web snapshots through the browser env.

        :param query: Archive search query.
        :param max_results: Maximum number of archive results to return.
        :param runtime_context: Optional runtime ops injected by the engine.
        """
        query = str(args.get("query", ""))
        max_results = int(args.get("max_results", 8))
        return self._ops(runtime_context).archive_search(
            query=query, max_results=max_results
        )


__all__ = [
    "WebSearch",
    "VisitURL",
    "PageDown",
    "PageUp",
    "FindInPage",
    "FindNext",
    "ArchiveSearch",
]
