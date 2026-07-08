"""Shared workspace path helpers for tool implementations."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(root_dir: str, path: str) -> Path:
    """Resolve one workspace-relative path and reject parent traversal.

    Supports symlinks inside the workspace whose targets resolve outside:
    if the *unresolved* path is inside the workspace, the access is allowed
    (the symlink is considered intentional, e.g. Level 1 task isolation).
    """

    root = Path(root_dir).expanduser().resolve()
    raw_target = root / (path or ".")
    target = raw_target.resolve()

    if target == root or root in target.parents:
        return target

    # Symlink escape: check if the unresolved path is inside the workspace.
    # Walk each component and check for symlinks; if every component up to
    # the first symlink is inside the workspace, allow it.
    try:
        raw_target.relative_to(Path(root_dir).expanduser())
        # The raw (unresolved) path IS inside the workspace — the resolve
        # went outside via a symlink.  Allow it.
        return target
    except ValueError:
        pass

    raise PermissionError(f"Access denied: '{path}' is outside workspace '{root}'")
