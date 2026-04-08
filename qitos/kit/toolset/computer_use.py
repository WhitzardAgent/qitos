"""Computer-use preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.gui import (
    Click,
    DoubleClick,
    Done,
    DragTo,
    Fail,
    Hotkey,
    KeyDown,
    KeyUp,
    MouseDown,
    MouseUp,
    MoveTo,
    PressKey,
    RightClick,
    Scroll,
    TypeText,
    Wait,
)
from qitos.kit.tool.toolset import BaseToolSet


class ComputerUseToolSet(BaseToolSet):
    """Canonical provider-neutral GUI/computer-use tool bundle."""

    name = "computer_use"
    version = "0.5"

    def tools(self) -> list[object]:
        return [
            MoveTo(),
            Click(),
            MouseDown(),
            MouseUp(),
            RightClick(),
            DoubleClick(),
            DragTo(),
            Scroll(),
            TypeText(),
            PressKey(),
            KeyDown(),
            KeyUp(),
            Hotkey(),
            Wait(),
            Done(),
            Fail(),
        ]


def computer_use_tools() -> ToolRegistry:
    """Build a registry containing the canonical computer-use action bundle."""
    return ToolRegistry().include_toolset(ComputerUseToolSet())


__all__ = ["ComputerUseToolSet", "computer_use_tools"]
