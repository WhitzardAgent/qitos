"""Tests for ToolPermissionSpec and ToolRegistry.export_permissions()."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from qitos.core.tool import ToolPermission, ToolPermissionSpec
from qitos.core.tool_registry import ToolRegistry


class TestToolPermissionSpec:
    def test_default_values(self) -> None:
        spec = ToolPermissionSpec(name="test_tool")
        assert spec.name == "test_tool"
        assert spec.description == ""
        assert spec.permissions == ToolPermission()
        assert spec.needs_approval is False
        assert spec.read_only is False
        assert spec.concurrency_safe is False
        assert spec.required_ops == []

    def test_frozen(self) -> None:
        spec = ToolPermissionSpec(name="test_tool")
        with pytest.raises(FrozenInstanceError):
            spec.name = "other"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        spec = ToolPermissionSpec(
            name="bash",
            description="Execute shell commands",
            permissions=ToolPermission(command=True),
            needs_approval=True,
            required_ops=["execute"],
        )
        d = spec.to_dict()
        assert d["name"] == "bash"
        assert d["description"] == "Execute shell commands"
        assert d["permissions"]["command"] is True
        assert d["permissions"]["filesystem_read"] is False
        assert d["needs_approval"] is True
        assert d["read_only"] is False
        assert d["required_ops"] == ["execute"]

    def test_to_dict_permissions_serialized(self) -> None:
        spec = ToolPermissionSpec(
            name="search",
            permissions=ToolPermission(network=True, filesystem_read=True),
        )
        d = spec.to_dict()
        assert d["permissions"]["network"] is True
        assert d["permissions"]["filesystem_read"] is True
        assert d["permissions"]["filesystem_write"] is False


class TestToolRegistryExportPermissions:
    def test_empty_registry(self) -> None:
        registry = ToolRegistry()
        assert registry.export_permissions() == []

    def test_export_permissions_for_registered_tool(self) -> None:
        registry = ToolRegistry()

        def my_tool(query: str) -> str:
            """Search for something."""
            return query

        registry.register(my_tool, meta=None)
        specs = registry.export_permissions()
        assert len(specs) == 1
        assert specs[0].name == "my_tool"
        assert isinstance(specs[0], ToolPermissionSpec)
        assert specs[0].to_dict()["name"] == "my_tool"

    def test_export_permissions_with_custom_permissions(self) -> None:
        from qitos.core.tool import ToolMeta

        registry = ToolRegistry()

        def bash_tool(cmd: str) -> str:
            """Run a command."""
            return ""

        meta = ToolMeta(
            name="bash",
            permissions=ToolPermission(command=True, filesystem_read=True),
            needs_approval=True,
            read_only=False,
        )
        registry.register(bash_tool, meta=meta)
        specs = registry.export_permissions()
        assert len(specs) == 1
        assert specs[0].name == "bash"
        assert specs[0].permissions.command is True
        assert specs[0].permissions.filesystem_read is True
        assert specs[0].needs_approval is True

    def test_export_permissions_multiple_tools(self) -> None:
        registry = ToolRegistry()

        def tool_a() -> str:
            """Tool A."""
            return ""

        def tool_b() -> str:
            """Tool B."""
            return ""

        registry.register(tool_a)
        registry.register(tool_b)
        specs = registry.export_permissions()
        assert len(specs) == 2
        names = [s.name for s in specs]
        assert "tool_a" in names
        assert "tool_b" in names
