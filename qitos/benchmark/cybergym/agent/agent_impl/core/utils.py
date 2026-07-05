"""Shared utility functions for CyberGym agent mixins.

These were previously module-level functions or @staticmethod methods in
agent.py. Centralising them here avoids circular imports and provides
a single import point for commonly-needed helpers.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from .constants import _BENCHMARK_NAME_RE


def clip(text: str, limit: int) -> str:
    """Truncate *text* to *limit* chars with an ellipsis marker."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def sanitize_model_text(text: str) -> str:
    """Replace benchmark name with 'task' to avoid leakage."""
    return _BENCHMARK_NAME_RE.sub("task", text)


def sanitize_tool_payload(value: Any) -> Any:
    """Recursively sanitize tool output to remove benchmark identifiers."""
    if isinstance(value, str):
        return sanitize_model_text(value)
    if isinstance(value, list):
        return [sanitize_tool_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_tool_payload(item) for item in value)
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "cwd":
                sanitized[key] = "."
            else:
                sanitized[key] = sanitize_tool_payload(item)
        return sanitized
    return value


def add_line_numbers_to_read_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Add cat -n style line numbers to a READ tool result's content field."""
    if result.get("status") != "success":
        return result
    content = result.get("content")
    if not isinstance(content, str) or not content.strip():
        return result
    offset = int(result.get("offset", 0) or 0)
    total_lines = int(result.get("total_lines", 0) or 0)
    limit = int(result.get("limit", 0) or 0)
    lines = content.split("\n")
    # Remove trailing empty line from split if content ends with newline
    if lines and lines[-1] == "":
        lines = lines[:-1]
    # Compute display width for line numbers
    last_line = offset + len(lines)
    width = len(str(last_line))
    numbered_lines = []
    for i, line in enumerate(lines):
        lineno = offset + i + 1
        numbered_lines.append(f"{lineno:>{width}}\t{line}")
    numbered_content = "\n".join(numbered_lines)
    # Add position header
    header = f"// Lines {offset + 1}-{last_line}"
    if total_lines > 0:
        header += f" of {total_lines}"
    if offset > 0:
        header += f" (offset={offset})"
    result = dict(result)
    result["content"] = f"{header}\n{numbered_content}"
    return result
