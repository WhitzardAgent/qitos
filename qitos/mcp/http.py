"""MCP server transport over Streamable HTTP."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .server import MCPServer, MCPToolInfo

logger = logging.getLogger(__name__)

# Try to import httpx; raise a helpful error at usage time if missing.
try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


def _require_httpx() -> None:
    if httpx is None:
        raise ImportError(
            "The httpx package is required for MCPServerStreamableHttp. "
            "Install it with: pip install httpx"
        )


class MCPServerStreamableHttp(MCPServer):
    """Connect to an MCP server via the Streamable HTTP transport.

    The MCP Streamable HTTP transport sends JSON-RPC requests as POST
    requests to the server's endpoint URL.  Each request body is a single
    JSON-RPC request object and the server returns the JSON-RPC response.

    Usage::

        server = MCPServerStreamableHttp(
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer token123"},
        )
        await server.connect()
        tools = await server.list_tools()
        result = await server.call_tool("search", {"query": "hello"})
        await server.cleanup()
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        name: Optional[str] = None,
    ) -> None:
        _require_httpx()
        self._url = url.rstrip("/")
        self._headers = dict(headers or {})
        self._name = name or f"http:{self._url}"
        self._client: Optional[httpx.AsyncClient] = None
        self._request_id = 0

    @property
    def name(self) -> str:
        return self._name

    # -- lifecycle ----------------------------------------------------------- #

    async def connect(self) -> None:
        """Open the HTTP client session and complete the MCP handshake."""
        _require_httpx()
        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

        # MCP initialization handshake
        init_result = await self._send_request(
            "initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qitos-mcp-client", "version": "0.1.0"},
            },
        )
        logger.debug("MCP HTTP initialize response: %s", init_result)

        # Send initialized notification
        await self._send_notification("notifications/initialized")
        logger.info("MCP HTTP handshake complete for %s", self._name)

    async def cleanup(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("MCP HTTP client closed for %s", self._name)

    # -- MCP operations ------------------------------------------------------ #

    async def list_tools(self) -> List[MCPToolInfo]:
        """Request the list of tools from the MCP server."""
        result = await self._send_request("tools/list", params={})
        tools_data = result.get("tools", [])
        return [
            MCPToolInfo(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_data
        ]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke a tool on the MCP server."""
        result = await self._send_request(
            "tools/call",
            params={"name": tool_name, "arguments": arguments},
        )
        content = result.get("content", [])
        if isinstance(content, list) and len(content) == 1:
            item = content[0]
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
        return result

    # -- internal JSON-RPC transport ----------------------------------------- #

    async def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON-RPC request via HTTP POST and return the result."""
        if self._client is None:
            raise RuntimeError("MCP server is not connected")

        request_id = await self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }
        logger.debug("MCP HTTP -> %s", json.dumps(payload))

        response = await self._client.post("", json=payload)
        response.raise_for_status()

        data = self._decode_response(response)
        logger.debug("MCP HTTP <- %s", json.dumps(data)[:500])

        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"MCP JSON-RPC error (code={err.get('code')}): {err.get('message')}"
            )

        return data.get("result", {})

    def _decode_response(self, response: Any) -> Dict[str, Any]:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "text/event-stream" in content_type:
            return self._decode_sse_response(response.text)
        return response.json()

    def _decode_sse_response(self, text: str) -> Dict[str, Any]:
        data_lines: List[str] = []
        last_error: Optional[json.JSONDecodeError] = None

        for raw_line in text.splitlines():
            line = raw_line.rstrip("\r")
            if not line:
                if data_lines:
                    parsed = self._parse_sse_data(data_lines)
                    if parsed is not None:
                        return parsed
                    try:
                        json.loads("\n".join(data_lines))
                    except json.JSONDecodeError as exc:
                        last_error = exc
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            parsed = self._parse_sse_data(data_lines)
            if parsed is not None:
                return parsed
            try:
                json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                last_error = exc

        if last_error is not None:
            raise ValueError(
                "MCP HTTP SSE data frame did not contain valid JSON"
            ) from last_error
        raise ValueError("MCP HTTP SSE response did not include a data frame")

    def _parse_sse_data(self, data_lines: List[str]) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            raise ValueError("MCP HTTP SSE data frame did not contain a JSON object")
        return data

    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification via HTTP POST."""
        if self._client is None:
            raise RuntimeError("MCP server is not connected")

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        logger.debug("MCP HTTP -> (notification) %s", json.dumps(payload))
        response = await self._client.post("", json=payload)
        # Notifications may return 202 or 204; don't enforce a specific status.
        if response.status_code >= 400:
            logger.warning(
                "MCP HTTP notification returned status %s", response.status_code
            )
