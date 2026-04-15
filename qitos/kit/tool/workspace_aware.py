"""Workspace-aware helpers reusable by file/codebase tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from .internal.workspace import resolve_workspace_path


class WorkspaceAwareMixin:
    """Provide path-safe workspace helpers and lightweight workspace context."""

    def __init__(self, workspace_root: str = ".", *, recent_limit: int = 20):
        self.workspace_root = str(Path(workspace_root).expanduser().resolve())
        self.recent_limit = max(1, int(recent_limit))
        self._recent_files: List[str] = []

    def resolve_path(self, path: str) -> str:
        """Resolve one relative path inside workspace boundaries."""
        resolved = resolve_workspace_path(self.workspace_root, path or ".")
        return str(resolved)

    def note_recent_file(self, path: str) -> None:
        """Track most-recently touched workspace-relative files."""
        try:
            resolved = Path(self.resolve_path(path))
            rel = str(resolved.relative_to(Path(self.workspace_root)))
        except Exception:
            return
        self._recent_files = [item for item in self._recent_files if item != rel]
        self._recent_files.append(rel)
        if len(self._recent_files) > self.recent_limit:
            self._recent_files = self._recent_files[-self.recent_limit :]

    def recent_files(self) -> List[str]:
        return list(self._recent_files)

    def workspace_summary(self, *, max_entries: int = 80, max_depth: int = 3) -> Dict[str, object]:
        """Return a concise tree/shape summary to prime model context."""
        root = Path(self.workspace_root)
        files: List[str] = []
        directories: List[str] = []
        max_entries = max(5, int(max_entries))
        max_depth = max(1, int(max_depth))
        for current_root, dirnames, filenames in os.walk(root):
            rel_root = Path(current_root).relative_to(root)
            depth = len(rel_root.parts)
            if depth > max_depth:
                dirnames[:] = []
                continue
            if str(rel_root) != ".":
                directories.append(str(rel_root))
            for filename in sorted(filenames):
                rel = str((rel_root / filename) if str(rel_root) != "." else Path(filename))
                files.append(rel)
                if len(files) >= max_entries:
                    break
            if len(files) >= max_entries:
                break
        return {
            "workspace_root": str(root),
            "directory_count": len(directories),
            "sample_directories": directories[: min(20, len(directories))],
            "sample_files": files[:max_entries],
            "recent_files": self.recent_files(),
            "path_rules": "Paths must stay within workspace_root. Use relative paths when possible.",
        }


__all__ = ["WorkspaceAwareMixin"]
