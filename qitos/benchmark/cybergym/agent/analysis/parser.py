"""Tree-sitter C/C++ parser with LRU cache.

Absorbed from tree-sitter-analyzer's core/parser.py, simplified for
cybergym_agent's C/C++-only focus and ParsedSource return type.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Optional

from .language_loader import LanguageLoader

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Byte-offset → line/column table (avoids unstable Point objects in
# tree-sitter 0.25.x + tree-sitter-c 0.24.x)
# ---------------------------------------------------------------------------

class _LineTable:
    """Pre-computed byte-offset → (line, column) mapping from source bytes.

    tree-sitter 0.25.x Node.start_point / Node.end_point can return wrong
    values or cause native SIGSEGV during AST traversal.  This table computes
    line/column from stable byte offsets instead.
    """
    __slots__ = ("_offsets",)

    def __init__(self, source: bytes) -> None:
        self._offsets = [0]  # byte offset of each line start
        for i, b in enumerate(source):
            if b == ord('\n'):
                self._offsets.append(i + 1)

    def line_col(self, byte_offset: int) -> tuple[int, int]:
        """Return (1-based line, 1-based column) for a byte offset."""
        import bisect
        line = bisect.bisect_right(self._offsets, byte_offset)  # 1-based
        col = byte_offset - self._offsets[line - 1] + 1
        return line, col


# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------
_PARSER_CACHE_SIZE = int(os.environ.get("TSA_PARSER_CACHE_SIZE", "2000"))


class _LRUCache(dict):
    """Minimal LRU dict — evicts the oldest key when full."""

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        self._maxsize = maxsize
        self._order: list[str] = []

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            self._order.remove(key)
        elif len(self._order) >= self._maxsize:
            oldest = self._order.pop(0)
            super().__delitem__(oldest)
        super().__setitem__(key, value)
        self._order.append(key)

    def __delitem__(self, key: str) -> None:
        self._order.remove(key)
        super().__delitem__(key)


# ---------------------------------------------------------------------------
# Error node collection
# ---------------------------------------------------------------------------

def _collect_error_nodes(node: Any, errors: list[dict[str, Any]], line_table: _LineTable | None = None) -> None:
    """Walk the tree collecting ERROR nodes."""
    if hasattr(node, "type") and node.type == "ERROR":
        text = ""
        if node.text:
            text = node.text.decode("utf-8", errors="replace")
        if line_table is not None:
            sl, sc = line_table.line_col(node.start_byte)
            el, ec = line_table.line_col(node.end_byte)
            errors.append({
                "type": "ERROR",
                "start_line": sl, "start_column": sc,
                "end_line": el, "end_column": ec,
                "text": text,
            })
        else:
            # Fallback: store byte offsets only (should not normally happen)
            errors.append({
                "type": "ERROR",
                "start_byte": node.start_byte,
                "end_byte": node.end_byte,
                "text": text,
            })
    if hasattr(node, "children"):
        for child in node.children:
            _collect_error_nodes(child, errors, line_table)


def _count_errors(root: Any) -> int:
    """Count ERROR and missing nodes using an explicit stack."""
    count = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if cur.type == "ERROR" or cur.is_missing:
            count += 1
        if hasattr(cur, "children"):
            for child in cur.children:
                stack.append(child)
    return count


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    """Tree-sitter parser wrapper for C/C++ with file-level caching."""

    _cache: _LRUCache = _LRUCache(maxsize=_PARSER_CACHE_SIZE)
    _stat_cache: dict[str, tuple[int, int, str, str]] = {}
    _hits = 0
    _misses = 0
    _stat_hits = 0

    def __init__(self, loader: Optional[LanguageLoader] = None) -> None:
        self._loader = loader or LanguageLoader()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    @classmethod
    def cache_info(cls) -> dict[str, Any]:
        return {
            "size": len(cls._cache),
            "maxsize": cls._cache._maxsize,
            "hits": cls._hits,
            "misses": cls._misses,
            "stat_hits": cls._stat_hits,
            "stat_cache_size": len(cls._stat_cache),
        }

    @classmethod
    def cache_clear(cls) -> None:
        cls._cache.clear()
        cls._stat_cache.clear()
        cls._hits = 0
        cls._misses = 0
        cls._stat_hits = 0

    # ------------------------------------------------------------------
    # File parsing
    # ------------------------------------------------------------------

    def parse_file(self, file_path: str | Path, language: str) -> Optional[Any]:
        """Parse a source file, returning a ParsedSource or None."""
        file_path_str = str(file_path)
        path_obj = Path(file_path_str)
        if not path_obj.exists():
            _LOG.debug("File not found: %s", file_path_str)
            return None

        try:
            # Cache lookup
            cache_key = self._cache_key(file_path_str, language)
            if cache_key is not None:
                cached = Parser._cache.get(cache_key)
                if cached is not None:
                    Parser._hits += 1
                    return cached

            # Read
            try:
                source_bytes = path_obj.read_bytes()
            except (PermissionError, OSError) as exc:
                _LOG.debug("Cannot read %s: %s", file_path_str, exc)
                return None

            # Parse
            result = self.parse_code(source_bytes, language)
            if result is not None and cache_key is not None:
                Parser._cache[cache_key] = result
            return result

        except Exception as exc:
            _LOG.debug("Unexpected error parsing %s: %s", file_path_str, exc)
            return None

    # ------------------------------------------------------------------
    # Code parsing (core)
    # ------------------------------------------------------------------

    def parse_code(
        self,
        source: bytes | str,
        language: str,
        *,
        line_offset: int = 0,
        transparent_boolean_macros: frozenset[str] = frozenset(),
        noreturn_macros: frozenset[str] = frozenset(),
        source_macros: frozenset[str] = frozenset(),
    ) -> Optional[Any]:
        """Parse source code, returning a ParsedSource or None.

        Accepts both bytes and str. When str, encodes as UTF-8.
        """
        if isinstance(source, str):
            source = source.encode("utf-8", errors="replace")

        if not self._loader.is_language_available(language):
            _LOG.debug("Language not available: %s", language)
            return None

        ts_parser = self._loader.create_parser(language)
        if ts_parser is None:
            return None

        try:
            tree = ts_parser.parse(source)
        except Exception as exc:
            _LOG.debug("Parse failed for lang=%s: %s", language, exc)
            return None

        if tree is None or tree.root_node is None:
            return None

        root = tree.root_node
        error_count = _count_errors(root)
        line_table = _LineTable(source)

        # Import here to avoid circular imports at module level.
        from cybergym_agent.analysis.constraints.ast import ParsedSource

        return ParsedSource(
            source=source,
            root=root,
            language=language,
            has_error=bool(root.has_error),
            error_count=error_count,
            line_offset=max(0, int(line_offset or 0)),
            transparent_boolean_macros=transparent_boolean_macros,
            noreturn_macros=noreturn_macros,
            source_macros=source_macros,
            _tree_ref=tree,
            _line_table=line_table,
        )

    # ------------------------------------------------------------------
    # Error extraction
    # ------------------------------------------------------------------

    @staticmethod
    def get_parse_errors(tree: Any, line_table: _LineTable | None = None) -> list[dict[str, Any]]:
        """Extract ERROR nodes from a tree-sitter Tree."""
        errors: list[dict[str, Any]] = []
        if tree and tree.root_node:
            _collect_error_nodes(tree.root_node, errors, line_table)
        return errors

    # ------------------------------------------------------------------
    # Language support
    # ------------------------------------------------------------------

    def is_language_supported(self, language: str) -> bool:
        return self._loader.is_language_available(language)

    def get_supported_languages(self) -> list[str]:
        return self._loader.get_supported_languages()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cache_key(self, file_path_str: str, language: str) -> Optional[str]:
        """Return a cache key based on mtime+size, or None on stat error."""
        try:
            stat = os.stat(file_path_str)
            mtime_ns = int(stat.st_mtime_ns)
            size = int(stat.st_size)
        except (OSError, TypeError):
            return None

        stat_entry = Parser._stat_cache.get(file_path_str)
        if (
            stat_entry is not None
            and stat_entry[0] == mtime_ns
            and stat_entry[1] == size
            and stat_entry[2] == language
        ):
            Parser._stat_hits += 1
            return str(stat_entry[3])

        key_string = f"{file_path_str}:{mtime_ns}:{size}:{language}"
        cache_key = hashlib.sha256(key_string.encode("utf-8")).hexdigest()
        Parser._stat_cache[file_path_str] = (mtime_ns, size, language, cache_key)
        return cache_key
