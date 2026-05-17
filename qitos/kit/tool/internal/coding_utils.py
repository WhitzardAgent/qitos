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
    "is_image_file",
    "is_pdf_file",
    "is_notebook_file",
    "read_image_as_base64",
    "read_pdf_text",
    "read_notebook_cells",
    "resolve_tool_workspace_path",
    "truncate_text",
    "utc_now",
]


# ── Multimodal file detection helpers ─────────────────────────────────────────

_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
)
_PDF_EXTENSIONS = frozenset({".pdf"})
_NOTEBOOK_EXTENSIONS = frozenset({".ipynb"})


def is_image_file(path: str) -> bool:
    """Check if a file path looks like an image."""
    return Path(path).suffix.lower() in _IMAGE_EXTENSIONS


def is_pdf_file(path: str) -> bool:
    """Check if a file path looks like a PDF."""
    return Path(path).suffix.lower() in _PDF_EXTENSIONS


def is_notebook_file(path: str) -> bool:
    """Check if a file path looks like a Jupyter notebook."""
    return Path(path).suffix.lower() in _NOTEBOOK_EXTENSIONS


def read_image_as_base64(path: str) -> Optional[str]:
    """Read an image file and return its base64-encoded data URL.

    Returns None if the file cannot be read or Pillow is not available.
    """
    import base64

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    suffix = Path(path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
        ".ico": "image/x-icon",
    }
    mime = mime_map.get(suffix, "application/octet-stream")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def read_pdf_text(path: str, pages: Optional[str] = None) -> Optional[str]:
    """Read a PDF file and return its text content.

    :param path: Path to the PDF file.
    :param pages: Page range string (e.g., "1-5", "3"). None = all pages.
    :returns: Extracted text, or None if PyPDF2 is not available.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return None

    try:
        reader = PdfReader(path)
    except Exception:
        return None

    # Parse page range
    page_indices = _parse_page_range(pages, len(reader.pages))
    parts: list[str] = []
    for idx in page_indices:
        try:
            text = reader.pages[idx].extract_text() or ""
            parts.append(f"--- Page {idx + 1} ---\n{text}")
        except Exception:
            parts.append(f"--- Page {idx + 1} ---\n[Could not extract text]")

    return "\n\n".join(parts)


def read_notebook_cells(path: str) -> Optional[str]:
    """Read a Jupyter notebook and return all cells with outputs.

    :param path: Path to the .ipynb file.
    :returns: Formatted cell contents, or None if nbformat is not available.
    """
    try:
        import nbformat
    except ImportError:
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
    except Exception:
        return None

    parts: list[str] = []
    for i, cell in enumerate(nb.cells):
        cell_type = cell.get("cell_type", "unknown")
        source = cell.get("source", "")
        header = f"Cell {i} ({cell_type})"
        parts.append(f"--- {header} ---\n{source}")

        # Include outputs for code cells
        outputs = cell.get("outputs", [])
        if outputs:
            output_parts: list[str] = []
            for out in outputs:
                out_type = out.get("output_type", "")
                if "text" in out:
                    output_parts.append(str(out["text"]))
                elif "data" in out:
                    data = out["data"]
                    if "text/plain" in data:
                        output_parts.append(str(data["text/plain"]))
                    elif "image/png" in data:
                        output_parts.append("[image output]")
            if output_parts:
                parts.append("Output:\n" + "\n".join(output_parts))

    return "\n\n".join(parts)


def _parse_page_range(
    pages: Optional[str], total: int
) -> list[int]:
    """Parse a page range string into a list of 0-based page indices."""
    if pages is None:
        return list(range(total))
    indices: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start_idx = max(0, int(start.strip()) - 1)
            end_idx = min(total, int(end.strip()))
            indices.extend(range(start_idx, end_idx))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total:
                indices.append(idx)
    return indices
