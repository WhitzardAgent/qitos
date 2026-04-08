"""Atomic shell execution tools."""

from __future__ import annotations

from qitos.kit.tool.internal.coding_impl import CodingToolSet
from qitos.kit.tool.internal.delegating import DelegatingTool


class RunCommand(DelegatingTool):
    def __init__(self, workspace_root: str = ".", shell_timeout: int = 30):
        super().__init__(
            CodingToolSet(
                workspace_root=workspace_root,
                shell_timeout=shell_timeout,
            ).run_command
        )


__all__ = ["RunCommand"]
