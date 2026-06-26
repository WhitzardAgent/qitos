"""Tests for MCP Server support.

Covers:
- MCPToolInfo dataclass
- ToolFilter matches logic
- Schema conversion (convert_mcp_schema_to_tool_spec)
- Internal helpers (_map_type, _resolve_refs, _convert_property)
- Bridge (mcp_server_to_function_tools) with mock servers
- MCPServerStdio construction and cleanup without live process
- MCPServerStreamableHttp construction without live server
- MCPServer ABC contract
- ToolRegistry integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qitos.core.tool import FunctionTool, ToolSpec
import qitos.mcp.http as http_transport
from qitos.mcp import (
    MCPServer,
    MCPToolInfo,
    MCPServerStdio,
    MCPServerStreamableHttp,
    ToolFilter,
    mcp_server_to_function_tools,
)
from qitos.mcp.schema_convert import (
    _convert_property,
    _map_type,
    _resolve_refs,
    convert_mcp_schema_to_tool_spec,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _MockMCPServer(MCPServer):
    """In-memory mock MCP server for testing the bridge and schema convert."""

    def __init__(
        self,
        tools: Optional[List[MCPToolInfo]] = None,
        name: str = "mock",
        call_results: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._name = name
        self._tools = tools or []
        self._call_results = call_results or {}
        self._connected = False
        self._cleaned_up = False

    @property
    def name(self) -> str:
        return self._name

    async def connect(self) -> None:
        self._connected = True

    async def cleanup(self) -> None:
        self._cleaned_up = True

    async def list_tools(self) -> List[MCPToolInfo]:
        return list(self._tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name in self._call_results:
            return self._call_results[tool_name]
        return {"status": "ok", "tool": tool_name, "arguments": arguments}


# --------------------------------------------------------------------------- #
# 1. MCPToolInfo
# --------------------------------------------------------------------------- #


class TestMCPToolInfo:
    """Test MCPToolInfo dataclass."""

    def test_basic_creation(self) -> None:
        info = MCPToolInfo(
            name="read_file",
            description="Read a file from disk",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        )
        assert info.name == "read_file"
        assert info.description == "Read a file from disk"
        assert "properties" in info.input_schema

    def test_defaults(self) -> None:
        info = MCPToolInfo(name="tool_a")
        assert info.description == ""
        assert info.input_schema == {}


# --------------------------------------------------------------------------- #
# 2. MCPServer ABC
# --------------------------------------------------------------------------- #


class TestMCPServerABC:
    """Test MCPServer abstract interface."""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            MCPServer()

    def test_subclass_must_implement(self) -> None:
        class IncompleteServer(MCPServer):
            pass

        with pytest.raises(TypeError):
            IncompleteServer()

    def test_concrete_subclass(self) -> None:
        class FakeServer(MCPServer):
            @property
            def name(self) -> str:
                return "fake"

            async def connect(self):
                pass

            async def cleanup(self):
                pass

            async def list_tools(self):
                return []

            async def call_tool(self, tool_name, arguments):
                return None

        server = FakeServer()
        assert server.name == "fake"

    def test_mock_server_fulfills_contract(self) -> None:
        server = _MockMCPServer(name="test")
        assert server.name == "test"


# --------------------------------------------------------------------------- #
# 3. ToolFilter
# --------------------------------------------------------------------------- #


class TestToolFilter:
    """Test MCP ToolFilter."""

    def test_no_filter_passes_all(self) -> None:
        f = ToolFilter()
        assert f.matches("anything") is True
        assert f.matches("other") is True

    def test_allowed_list(self) -> None:
        f = ToolFilter(allowed_tool_names={"search", "read"})
        assert f.matches("search") is True
        assert f.matches("read") is True
        assert f.matches("write") is False

    def test_blocked_list(self) -> None:
        f = ToolFilter(blocked_tool_names={"dangerous"})
        assert f.matches("safe_tool") is True
        assert f.matches("dangerous") is False

    def test_allowed_and_blocked_combined(self) -> None:
        # Name must be in allowed AND not in blocked
        f = ToolFilter(
            allowed_tool_names={"search", "dangerous"},
            blocked_tool_names={"dangerous"},
        )
        assert f.matches("search") is True
        assert f.matches("dangerous") is False
        assert f.matches("other") is False

    def test_filter_func_overrides_all(self) -> None:
        f = ToolFilter(
            allowed_tool_names={"search"},
            blocked_tool_names={"search"},
            filter_func=lambda name: name.startswith("fs_"),
        )
        assert f.matches("fs_read") is True
        assert f.matches("search") is False  # filter_func takes priority

    def test_filter_func_false(self) -> None:
        f = ToolFilter(filter_func=lambda name: False)
        assert f.matches("any") is False

    def test_blocklist_overrides_allowlist(self) -> None:
        f = ToolFilter(allowed_tool_names={"read", "write"}, blocked_tool_names={"write"})
        assert f.matches("read") is True
        assert f.matches("write") is False  # blocked overrides


# --------------------------------------------------------------------------- #
# 4. Schema conversion
# --------------------------------------------------------------------------- #


class TestSchemaConvert:
    """Test MCP JSON Schema to ToolSpec conversion."""

    def test_simple_schema(self) -> None:
        tool = MCPToolInfo(
            name="read_file",
            description="Read a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.name == "read_file"
        assert spec.description == "Read a file"
        assert "path" in spec.parameters
        assert "path" in spec.required
        assert spec.parameters["path"]["type"] == "string"
        assert spec.parameters["path"]["description"] == "File path"

    def test_name_prefix(self) -> None:
        tool = MCPToolInfo(
            name="read",
            description="Read",
            input_schema={"type": "object", "properties": {}},
        )
        spec = convert_mcp_schema_to_tool_spec(tool, name_prefix="fs")
        assert spec.name == "fs__read"

    def test_multiple_types(self) -> None:
        tool = MCPToolInfo(
            name="multi",
            description="Multi type tool",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "count": {"type": "integer", "description": "Max results"},
                    "ratio": {"type": "number"},
                    "flag": {"type": "boolean"},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "config": {"type": "object", "properties": {"key": {"type": "string"}}},
                },
                "required": ["query"],
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["query"]["type"] == "string"
        assert spec.parameters["count"]["type"] == "integer"
        assert spec.parameters["ratio"]["type"] == "number"
        assert spec.parameters["flag"]["type"] == "boolean"
        assert spec.parameters["items"]["type"] == "array"
        assert spec.parameters["items"]["items"]["type"] == "string"
        assert spec.parameters["config"]["type"] == "object"
        assert spec.parameters["config"]["properties"]["key"]["type"] == "string"

    def test_no_properties(self) -> None:
        tool = MCPToolInfo(
            name="list",
            description="List all",
            input_schema={"type": "object", "properties": {}},
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters == {}
        assert spec.required == []

    def test_empty_schema(self) -> None:
        tool = MCPToolInfo(name="no_args", description="No args tool")
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.name == "no_args"
        assert spec.parameters == {}
        assert spec.required == []

    def test_nullable_anyof(self) -> None:
        tool = MCPToolInfo(
            name="nullable_tool",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["query"]["type"] == "string"
        assert spec.parameters["query"]["nullable"] is True

    def test_enum_values(self) -> None:
        tool = MCPToolInfo(
            name="enum_tool",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "slow"]},
                },
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["mode"]["enum"] == ["fast", "slow"]

    def test_ref_resolution(self) -> None:
        tool = MCPToolInfo(
            name="ref_tool",
            input_schema={
                "type": "object",
                "properties": {
                    "item": {"$ref": "#/$defs/Item"},
                },
                "$defs": {
                    "Item": {"type": "object", "properties": {"id": {"type": "string"}}},
                },
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["item"]["type"] == "object"
        assert spec.parameters["item"]["properties"]["id"]["type"] == "string"

    def test_default_values(self) -> None:
        tool = MCPToolInfo(
            name="defaults_tool",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["limit"]["default"] == 10

    def test_string_format(self) -> None:
        tool = MCPToolInfo(
            name="date_tool",
            input_schema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "format": "date"},
                },
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.parameters["date"]["format"] == "date"

    def test_additional_properties_false(self) -> None:
        tool = MCPToolInfo(
            name="strict_obj",
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        )
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.input_schema.get("additionalProperties") is False

    def test_read_only_default(self) -> None:
        tool = MCPToolInfo(name="ro_tool", input_schema={"type": "object"})
        spec = convert_mcp_schema_to_tool_spec(tool)
        assert spec.read_only is True


# --------------------------------------------------------------------------- #
# 5. Internal helpers
# --------------------------------------------------------------------------- #


class TestInternalHelpers:
    def test_map_type_known(self) -> None:
        assert _map_type({"type": "string"}) == "string"
        assert _map_type({"type": "integer"}) == "integer"
        assert _map_type({"type": "number"}) == "number"
        assert _map_type({"type": "boolean"}) == "boolean"
        assert _map_type({"type": "array"}) == "array"
        assert _map_type({"type": "object"}) == "object"

    def test_map_type_unknown(self) -> None:
        assert _map_type({"type": "custom"}) == "any"
        assert _map_type({}) == "any"

    def test_map_type_null(self) -> None:
        assert _map_type({"type": "null"}) == "any"

    def test_resolve_refs_simple(self) -> None:
        defs = {"Foo": {"type": "string"}}
        result = _resolve_refs({"$ref": "#/$defs/Foo"}, defs)
        assert result == {"type": "string"}

    def test_resolve_refs_nested(self) -> None:
        defs = {"Bar": {"$ref": "#/$defs/Baz"}, "Baz": {"type": "integer"}}
        result = _resolve_refs({"$ref": "#/$defs/Bar"}, defs)
        assert result == {"type": "integer"}

    def test_resolve_refs_depth_limit(self) -> None:
        defs: Dict[str, Any] = {}
        defs["A"] = {"$ref": "#/$defs/A"}
        result = _resolve_refs({"$ref": "#/$defs/A"}, defs)
        # Should not infinite loop; returns something
        assert isinstance(result, dict)

    def test_resolve_refs_unresolvable(self) -> None:
        defs = {}
        result = _resolve_refs({"$ref": "#/$defs/Missing"}, defs)
        # Returns the original ref dict since the target is not found
        assert "$ref" in result

    def test_convert_property_allof_single(self) -> None:
        result = _convert_property({"allOf": [{"type": "string", "description": "desc"}]})
        assert result["type"] == "string"
        assert result["description"] == "desc"

    def test_convert_property_allof_multiple(self) -> None:
        result = _convert_property({
            "allOf": [
                {"type": "object", "description": "base"},
                {"description": "extra"},
            ],
        })
        # allOf with mixed schemas falls back to "any" since we can't
        # fully merge arbitrary JSON Schema compositions
        assert result["type"] in ("object", "any")

    def test_convert_property_oneof(self) -> None:
        result = _convert_property({"oneOf": [{"type": "integer"}, {"type": "string"}]})
        assert result["type"] == "integer"

    def test_convert_property_additional_properties(self) -> None:
        result = _convert_property({
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "additionalProperties": False,
        })
        assert result["type"] == "object"
        assert result["additionalProperties"] is False

    def test_convert_property_anyof_multiple_non_null(self) -> None:
        result = _convert_property({
            "anyOf": [{"type": "string"}, {"type": "integer"}],
        })
        # Falls through to first non-null variant
        assert result["type"] == "string"


# --------------------------------------------------------------------------- #
# 6. Bridge with mock server
# --------------------------------------------------------------------------- #


class TestBridge:
    @pytest.mark.asyncio
    async def test_bridge_produces_function_tools(self) -> None:
        tools = [
            MCPToolInfo(
                name="search",
                description="Search for items",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            MCPToolInfo(
                name="count",
                description="Count items",
                input_schema={
                    "type": "object",
                    "properties": {"filter": {"type": "string"}},
                    "required": [],
                },
            ),
        ]
        server = _MockMCPServer(tools=tools)
        result = await mcp_server_to_function_tools(server)
        assert len(result) == 2
        assert all(isinstance(t, FunctionTool) for t in result)
        names = {t.name for t in result}
        assert "search" in names
        assert "count" in names

    @pytest.mark.asyncio
    async def test_bridge_with_allowed_filter(self) -> None:
        tools = [
            MCPToolInfo(name="search", input_schema={"type": "object"}),
            MCPToolInfo(name="write", input_schema={"type": "object"}),
            MCPToolInfo(name="delete", input_schema={"type": "object"}),
        ]
        server = _MockMCPServer(tools=tools)
        f = ToolFilter(allowed_tool_names={"search"})
        result = await mcp_server_to_function_tools(server, tool_filter=f)
        assert len(result) == 1
        assert result[0].name == "search"

    @pytest.mark.asyncio
    async def test_bridge_with_blocked_filter(self) -> None:
        tools = [
            MCPToolInfo(name="safe", input_schema={"type": "object"}),
            MCPToolInfo(name="dangerous", input_schema={"type": "object"}),
        ]
        server = _MockMCPServer(tools=tools)
        f = ToolFilter(blocked_tool_names={"dangerous"})
        result = await mcp_server_to_function_tools(server, tool_filter=f)
        assert len(result) == 1
        assert result[0].name == "safe"

    @pytest.mark.asyncio
    async def test_bridge_with_name_prefix(self) -> None:
        tools = [
            MCPToolInfo(name="search", description="Search", input_schema={"type": "object"}),
        ]
        server = _MockMCPServer(tools=tools)
        result = await mcp_server_to_function_tools(server, name_prefix="db")
        assert len(result) == 1
        assert result[0].name == "db__search"
        assert result[0].spec.name == "db__search"

    @pytest.mark.asyncio
    async def test_bridge_tool_spec_has_correct_schema(self) -> None:
        tools = [
            MCPToolInfo(
                name="greet",
                description="Greet someone",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Person name"},
                    },
                    "required": ["name"],
                },
            ),
        ]
        server = _MockMCPServer(tools=tools)
        result = await mcp_server_to_function_tools(server)
        assert len(result) == 1
        spec = result[0].spec
        assert spec.name == "greet"
        assert spec.description == "Greet someone"
        assert "name" in spec.parameters
        assert spec.parameters["name"]["type"] == "string"
        assert spec.required == ["name"]

    @pytest.mark.asyncio
    async def test_bridge_empty_tools(self) -> None:
        server = _MockMCPServer(tools=[])
        result = await mcp_server_to_function_tools(server)
        assert result == []


# --------------------------------------------------------------------------- #
# 7. MCPServerStdio (no live subprocess)
# --------------------------------------------------------------------------- #


class TestMCPServerStdio:
    def test_construction(self) -> None:
        server = MCPServerStdio(
            command="npx",
            args=["-y", "@mcp/server"],
            env={"KEY": "val"},
            cwd="/tmp",
            name="test-stdio",
        )
        assert server.name == "test-stdio"
        assert server._process is None

    def test_default_name(self) -> None:
        server = MCPServerStdio(command="python")
        assert server.name == "stdio:python"

    @pytest.mark.asyncio
    async def test_cleanup_without_connect(self) -> None:
        server = MCPServerStdio(command="echo")
        # Should not raise even if never connected
        await server.cleanup()

    @pytest.mark.asyncio
    async def test_operations_without_connect_raise(self) -> None:
        server = MCPServerStdio(command="echo")
        with pytest.raises(RuntimeError, match="not connected"):
            await server.list_tools()
        with pytest.raises(RuntimeError, match="not connected"):
            await server.call_tool("x", {})


# --------------------------------------------------------------------------- #
# 8. MCPServerStreamableHttp (no live server)
# --------------------------------------------------------------------------- #


class TestMCPServerStreamableHttp:
    def _patch_http_client(self, monkeypatch, handler):
        httpx = http_transport.httpx
        if httpx is None:
            pytest.skip("httpx not installed")

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", make_client)
        return httpx

    def _sse_response(self, httpx, request_id: int, result: Dict[str, Any]):
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        body = (
            "event: message\n"
            f"data: {json.dumps(payload)}\n\n"
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=body,
        )

    def test_streamable_http_parses_sse_json_rpc_responses(self, monkeypatch) -> None:
        def handler(request):
            payload = json.loads(request.content or b"{}")
            request_id = payload.get("id")
            if request_id is None:
                return httpx.Response(202)
            if payload.get("method") == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sse-repro", "version": "0.0.1"},
                }
            elif payload.get("method") == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "nmap_scan",
                            "description": "Run nmap",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            else:
                result = {}
            return self._sse_response(httpx, request_id, result)

        httpx = self._patch_http_client(monkeypatch, handler)
        server = MCPServerStreamableHttp(url="http://localhost:8080/mcp")

        async def exercise():
            try:
                await server.connect()
                return await server.list_tools()
            finally:
                await server.cleanup()

        tools = asyncio.run(exercise())
        assert [tool.name for tool in tools] == ["nmap_scan"]

    def test_streamable_http_follows_redirect_before_sse_response(
        self, monkeypatch
    ) -> None:
        paths: List[str] = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/mcp/":
                return httpx.Response(307, headers={"Location": "/mcp"})
            payload = json.loads(request.content or b"{}")
            request_id = payload.get("id")
            if request_id is None:
                return httpx.Response(202)
            if payload.get("method") == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "redirect-repro", "version": "0.0.1"},
                }
            elif payload.get("method") == "tools/list":
                result = {"tools": []}
            else:
                result = {}
            return self._sse_response(httpx, request_id, result)

        httpx = self._patch_http_client(monkeypatch, handler)
        server = MCPServerStreamableHttp(
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer reprotoken123"},
        )

        async def exercise():
            try:
                await server.connect()
                return await server.list_tools()
            finally:
                await server.cleanup()

        assert asyncio.run(exercise()) == []
        assert "/mcp/" in paths
        assert "/mcp" in paths

    def test_construction(self) -> None:
        try:
            server = MCPServerStreamableHttp(
                url="http://localhost:8080/mcp",
                headers={"Authorization": "Bearer tok"},
                name="test-http",
            )
            assert server.name == "test-http"
            assert server._client is None
        except ImportError:
            pytest.skip("httpx not installed")

    def test_default_name(self) -> None:
        try:
            server = MCPServerStreamableHttp(url="http://localhost:8080/mcp")
            assert server.name == "http:http://localhost:8080/mcp"
        except ImportError:
            pytest.skip("httpx not installed")

    @pytest.mark.asyncio
    async def test_cleanup_without_connect(self) -> None:
        try:
            server = MCPServerStreamableHttp(url="http://localhost:8080/mcp")
            await server.cleanup()  # should not raise
        except ImportError:
            pytest.skip("httpx not installed")

    @pytest.mark.asyncio
    async def test_operations_without_connect_raise(self) -> None:
        try:
            server = MCPServerStreamableHttp(url="http://localhost:8080/mcp")
            with pytest.raises(RuntimeError, match="not connected"):
                await server.list_tools()
            with pytest.raises(RuntimeError, match="not connected"):
                await server.call_tool("x", {})
        except ImportError:
            pytest.skip("httpx not installed")


# --------------------------------------------------------------------------- #
# 9. ToolRegistry integration
# --------------------------------------------------------------------------- #


class TestToolRegistryIntegration:
    @pytest.mark.asyncio
    async def test_register_mcp_tools_in_registry(self) -> None:
        from qitos.core.tool_registry import ToolRegistry

        tools = [
            MCPToolInfo(
                name="search",
                description="Search items",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
        ]
        server = _MockMCPServer(tools=tools)
        function_tools = await mcp_server_to_function_tools(server)

        registry = ToolRegistry()
        for ft in function_tools:
            registry.register(ft)

        assert "search" in registry
        tool = registry.get("search")
        assert tool is not None
        assert tool.spec.description == "Search items"

    @pytest.mark.asyncio
    async def test_register_prefixed_mcp_tools_in_registry(self) -> None:
        from qitos.core.tool_registry import ToolRegistry

        tools = [
            MCPToolInfo(
                name="search",
                description="Search items",
                input_schema={"type": "object"},
            ),
        ]
        server = _MockMCPServer(tools=tools)
        function_tools = await mcp_server_to_function_tools(server, name_prefix="db")

        registry = ToolRegistry()
        for ft in function_tools:
            registry.register(ft)

        assert "db__search" in registry
