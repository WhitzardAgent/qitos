"""Shared helpers for the canonical coding toolset."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Any, Dict, Optional

from qitos.kit.tool.internal.workspace import resolve_workspace_path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_tool_workspace_path(root_dir: str, path: str) -> Path:
    return resolve_workspace_path(root_dir, path)


def detect_line_ending(raw: bytes) -> str:
    return "\r\n" if b"\r\n" in raw else "\n"


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n... [truncated]", True


def build_diff(old_content: str, new_content: str, path: str) -> str:
    lines = list(
        unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    return "".join(lines)


def default_rule_scope(args: Dict[str, Any]) -> Optional[str]:
    for key in ("path", "filename", "url"):
        value = args.get(key)
        if value:
            return str(value)
    return None


__all__ = [
    "build_diff",
    "default_rule_scope",
    "detect_line_ending",
    "resolve_tool_workspace_path",
    "truncate_text",
    "utc_now",
]
