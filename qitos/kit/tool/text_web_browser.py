"""Compatibility shim for text-browser style web tools.

Prefer importing from `qitos.kit.tool.browser` in new code.
"""

from qitos.kit.tool.browser import (
    ArchiveSearch,
    FindInPage,
    FindNext,
    PageDown,
    PageUp,
    VisitURL,
    WebSearch,
)

__all__ = [
    "WebSearch",
    "VisitURL",
    "PageDown",
    "PageUp",
    "FindInPage",
    "FindNext",
    "ArchiveSearch",
]
