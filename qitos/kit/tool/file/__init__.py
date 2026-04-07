"""Atomic file inspection and editing tools."""

from __future__ import annotations

from qitos.kit.tool.internal.coding_impl import CodingToolSet
from qitos.kit.tool.internal.delegating import DelegatingTool


class FileReadV2(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).file_read_v2)


class FileEditV2(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).file_edit_v2)


class ReadFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).read_file)


class ViewFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).view)


class ListFiles(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).list_files)


class WriteFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).write_file)


class CreateFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).create)


class StrReplace(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).str_replace)


class InsertText(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).insert)


class ReplaceLines(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).replace_lines)


class AppendFile(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).append_file)


class MakeDirectory(DelegatingTool):
    def __init__(self, workspace_root: str = "."):
        super().__init__(CodingToolSet(workspace_root=workspace_root).make_directory)


__all__ = [
    "AppendFile",
    "CreateFile",
    "FileEditV2",
    "FileReadV2",
    "InsertText",
    "ListFiles",
    "MakeDirectory",
    "ReadFile",
    "ReplaceLines",
    "StrReplace",
    "ViewFile",
    "WriteFile",
]
