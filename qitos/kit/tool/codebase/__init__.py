"""Atomic codebase discovery and search tools."""

from __future__ import annotations

from qitos.kit.tool.internal.coding_impl import CodingToolSet
from qitos.kit.tool.internal.delegating import DelegatingTool


class GlobV2(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).glob_v2)


class GlobFiles(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).glob_files)


class GrepV2(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).grep_v2)


class GrepFiles(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).grep_files)


class ReadFileRange(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).read_file_range)


class SearchInFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).search)


class ListTree(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).list_tree)


__all__ = [
    "GlobFiles",
    "GlobV2",
    "GrepFiles",
    "GrepV2",
    "ListTree",
    "ReadFileRange",
    "SearchInFile",
]
