"""Shared workspace path helpers for tool implementations."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(root_dir: str, path: str) -> Path:
    """Resolve one workspace-relative path and reject parent traversal."""

    root = Path(root_dir).expanduser().resolve()
    target = (root / (path or ".")).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"Access denied: '{path}' is outside workspace '{root}'")
    return target
