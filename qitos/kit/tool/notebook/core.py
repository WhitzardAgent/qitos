"""Atomic Jupyter notebook tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec
from qitos.kit.tool.internal.workspace import resolve_workspace_path


def _load_notebook(root_dir: str, path: str) -> tuple[Path, Dict[str, Any]]:
    resolved = Path(resolve_workspace_path(root_dir, path))
    with resolved.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if (
        not isinstance(data, dict)
        or "cells" not in data
        or not isinstance(data["cells"], list)
    ):
        raise ValueError(f"Invalid notebook format: {path}")
    return resolved, data


def _dump_notebook(resolved: Path, data: Dict[str, Any]) -> None:
    with resolved.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _normalize_source(source: Any) -> str:
    if isinstance(source, list):
        return "".join(str(item) for item in source)
    return str(source or "")


class ReadNotebook(BaseTool):
    """Read a slice of notebook cells from a `.ipynb` file in the workspace.

    Use this tool when the agent needs to inspect markdown or code cells without
    manually parsing notebook JSON.
    """

    def __init__(self, root_dir: str = "."):
        self._root_dir = os.path.abspath(root_dir)
        super().__init__(
            ToolSpec(
                name="read_notebook",
                description="Read notebook cells from a .ipynb file under workspace",
                parameters={
                    "path": {"type": "string"},
                    "cell_start": {"type": "integer"},
                    "cell_limit": {"type": "integer"},
                },
                required=["path"],
                permissions=ToolPermission(filesystem_read=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Read a window of cells from a notebook file.

        :param path: Notebook path relative to the workspace root.
        :param cell_start: Zero-based index of the first cell to return.
        :param cell_limit: Maximum number of cells to return.
        :param runtime_context: Optional runtime ops injected by the engine.

        Returns simplified cell records with index, type, and source text.
        """
        _ = runtime_context
        path = str(args.get("path", ""))
        cell_start = int(args.get("cell_start", 0))
        cell_limit = int(args.get("cell_limit", 20))
        try:
            resolved, data = _load_notebook(self._root_dir, path)
            cells = data.get("cells", [])
            start = max(0, int(cell_start))
            limit = max(1, int(cell_limit))
            selected = cells[start : start + limit]
            rendered = []
            for idx, cell in enumerate(selected, start=start):
                rendered.append(
                    {
                        "index": idx,
                        "cell_type": str(cell.get("cell_type", "")),
                        "source": _normalize_source(cell.get("source")),
                    }
                )
            return {
                "status": "success",
                "path": os.path.relpath(str(resolved), self._root_dir),
                "total_cells": len(cells),
                "cell_start": start,
                "cell_limit": limit,
                "cells": rendered,
                "has_more": start + limit < len(cells),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class ReplaceNotebookCell(BaseTool):
    """Replace the source content of one notebook cell.

    Use this tool to update an existing markdown or code cell in place while
    preserving the rest of the notebook structure.
    """

    def __init__(self, root_dir: str = "."):
        self._root_dir = os.path.abspath(root_dir)
        super().__init__(
            ToolSpec(
                name="replace_notebook_cell",
                description="Replace the source of one notebook cell",
                parameters={
                    "path": {"type": "string"},
                    "cell_index": {"type": "integer"},
                    "source": {"type": "string"},
                },
                required=["path", "cell_index", "source"],
                permissions=ToolPermission(filesystem_write=True, filesystem_read=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Replace the source text of one notebook cell.

        :param path: Notebook path relative to the workspace root.
        :param cell_index: Zero-based index of the cell to replace.
        :param source: New source text for the target cell.
        :param runtime_context: Optional runtime ops injected by the engine.

        Preserves the notebook structure and only rewrites the selected cell.
        """
        _ = runtime_context
        path = str(args.get("path", ""))
        cell_index = int(args.get("cell_index", 0))
        source = str(args.get("source", ""))
        try:
            resolved, data = _load_notebook(self._root_dir, path)
            cells = data["cells"]
            idx = int(cell_index)
            if idx < 0 or idx >= len(cells):
                return {"status": "error", "message": f"Cell index out of range: {idx}"}
            cells[idx]["source"] = source.splitlines(keepends=True)
            _dump_notebook(resolved, data)
            return {
                "status": "success",
                "path": os.path.relpath(str(resolved), self._root_dir),
                "cell_index": idx,
                "cell_type": str(cells[idx].get("cell_type", "")),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class InsertNotebookCell(BaseTool):
    """Insert a new notebook cell at a chosen position.

    Use this tool to add explanatory markdown, new code cells, or raw cells into
    an existing notebook without rebuilding the file manually.
    """

    def __init__(self, root_dir: str = "."):
        self._root_dir = os.path.abspath(root_dir)
        super().__init__(
            ToolSpec(
                name="insert_notebook_cell",
                description="Insert a notebook cell at a given index",
                parameters={
                    "path": {"type": "string"},
                    "cell_type": {"type": "string"},
                    "source": {"type": "string"},
                    "index": {"type": "integer"},
                },
                required=["path", "cell_type", "source"],
                permissions=ToolPermission(filesystem_write=True, filesystem_read=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Insert a new notebook cell into a `.ipynb` file.

        :param path: Notebook path relative to the workspace root.
        :param cell_type: Cell type to insert: `code`, `markdown`, or `raw`.
        :param source: Source text for the new cell.
        :param index: Zero-based insertion index, or `-1` to append.
        :param runtime_context: Optional runtime ops injected by the engine.

        Creates a valid notebook cell structure and returns the final inserted
        position.
        """
        _ = runtime_context
        path = str(args.get("path", ""))
        cell_type = str(args.get("cell_type", ""))
        source = str(args.get("source", ""))
        index = int(args.get("index", -1))
        normalized_type = str(cell_type or "").strip().lower()
        if normalized_type not in {"code", "markdown", "raw"}:
            return {
                "status": "error",
                "message": f"Unsupported cell_type: {normalized_type}",
            }
        try:
            resolved, data = _load_notebook(self._root_dir, path)
            cells = data["cells"]
            new_cell = {
                "cell_type": normalized_type,
                "metadata": {},
                "source": source.splitlines(keepends=True),
            }
            if normalized_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
            insert_at = (
                len(cells) if int(index) < 0 else min(max(0, int(index)), len(cells))
            )
            cells.insert(insert_at, new_cell)
            _dump_notebook(resolved, data)
            return {
                "status": "success",
                "path": os.path.relpath(str(resolved), self._root_dir),
                "cell_index": insert_at,
                "cell_type": normalized_type,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class NotebookToolSet:
    """Bundle common notebook inspection and editing tools."""

    name = "notebook"
    version = "1"

    def __init__(self, workspace_root: str = "."):
        self.read_notebook = ReadNotebook(root_dir=workspace_root)
        self.replace_notebook_cell = ReplaceNotebookCell(root_dir=workspace_root)
        self.insert_notebook_cell = InsertNotebookCell(root_dir=workspace_root)

    def setup(self, context: dict[str, Any]) -> None:
        _ = context

    def teardown(self, context: dict[str, Any]) -> None:
        _ = context

    def tools(self) -> list[Any]:
        return [
            self.read_notebook,
            self.replace_notebook_cell,
            self.insert_notebook_cell,
        ]


__all__ = [
    "ReadNotebook",
    "ReplaceNotebookCell",
    "InsertNotebookCell",
    "NotebookToolSet",
]
