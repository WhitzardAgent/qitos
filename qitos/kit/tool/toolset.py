"""ToolSet protocol and helpers for grouped capabilities with lifecycle."""

from __future__ import annotations

from typing import Any, Iterable, Protocol


class ToolSet(Protocol):
    """Protocol for grouped tools that may need setup and teardown hooks."""

    name: str
    version: str

    def setup(self, context: dict[str, Any]) -> None:
        """Prepare resources before runtime starts."""

    def teardown(self, context: dict[str, Any]) -> None:
        """Release resources after runtime ends."""

    def tools(self) -> list[Any]:
        """Return tool callables or BaseTool objects."""


class BaseToolSet:
    """Optional base class for reusable, lifecycle-aware tool bundles."""

    name: str = "toolset"
    version: str = "0"

    def setup(self, context: dict[str, Any]) -> None:
        """Prepare resources before runtime starts."""
        _ = context

    def teardown(self, context: dict[str, Any]) -> None:
        """Release resources after runtime ends."""
        _ = context

    def tools(self) -> list[Any]:
        """Return tool callables or BaseTool objects."""
        raise NotImplementedError


class StaticToolSet(BaseToolSet):
    """Simple reusable toolset that wraps a fixed list of tools."""

    def __init__(
        self, items: Iterable[Any], *, name: str = "toolset", version: str = "0"
    ):
        self.name = str(name)
        self.version = str(version)
        self._items = list(items)

    def tools(self) -> list[Any]:
        return list(self._items)


def toolset_from_tools(
    items: Iterable[Any], *, name: str = "toolset", version: str = "0"
) -> StaticToolSet:
    """Build a lightweight static toolset from a list of tools."""
    return StaticToolSet(items, name=name, version=version)


__all__ = ["BaseToolSet", "StaticToolSet", "ToolSet", "toolset_from_tools"]
