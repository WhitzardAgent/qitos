"""Robust Tree-sitter C/C++ parser backend.

Handles multiple tree-sitter Python binding API versions gracefully:
- older: Parser.set_language(Language(capsule))
- mid:   parser.language = Language(capsule)
- newer: Parser(Language(obj))

Replaces the previous direct tree-sitter integration that suffered from
SIGSEGV on malformed C/C++ input.
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Optional, Sequence

_LOG = logging.getLogger(__name__)

_LOCK = threading.Lock()
_LANGUAGES: dict[str, Any] = {}
_PARSERS: dict[str, Any] = {}
_INIT_ERROR: Optional[str] = None

# C/C++ grammar modules
_GRAMMAR_MODULES = {
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
}


def _init() -> Optional[str]:
    """Try to load C and C++ grammars.  Return error string or None."""
    global _INIT_ERROR
    if _LANGUAGES or _INIT_ERROR is not None:
        return _INIT_ERROR
    with _LOCK:
        if _LANGUAGES or _INIT_ERROR is not None:
            return _INIT_ERROR
        try:
            from tree_sitter import Language

            for lang_key, module_name in _GRAMMAR_MODULES.items():
                try:
                    mod = importlib.import_module(module_name)
                    lang_func = getattr(mod, "language", None)
                    if lang_func is None:
                        continue
                    capsule_or_lang = lang_func()
                    # Newer tree-sitter: language_func() returns a Language directly
                    if hasattr(capsule_or_lang, "__class__") and "Language" in str(
                        type(capsule_or_lang)
                    ):
                        _LANGUAGES[lang_key] = capsule_or_lang
                    else:
                        # Older: PyCapsule → wrap in Language
                        _LANGUAGES[lang_key] = Language(capsule_or_lang)
                except Exception as exc:
                    _LOG.debug("Failed to load grammar %s: %s", module_name, exc)
            if not _LANGUAGES:
                _INIT_ERROR = "No C/C++ grammars available"
        except Exception as exc:
            _INIT_ERROR = f"{type(exc).__name__}: {exc}"
    return _INIT_ERROR


def _get_parser(language: str) -> Any:
    """Return a cached tree-sitter Parser for *language*, or None."""
    if language in _PARSERS:
        return _PARSERS[language]
    ts_lang = _LANGUAGES.get(language)
    if ts_lang is None:
        return None
    try:
        from tree_sitter import Parser

        # Try the constructor form first (newer API)
        try:
            parser = Parser(ts_lang)
        except TypeError:
            # Fallback: create then set
            parser = Parser()
            if hasattr(parser, "set_language"):
                parser.set_language(ts_lang)
            elif hasattr(parser, "language"):
                parser.language = ts_lang
            else:
                return None
        _PARSERS[language] = parser
        return parser
    except Exception as exc:
        _LOG.debug("Failed to create Parser for %s: %s", language, exc)
        return None


def tsa_available() -> bool:
    """Return True if C/C++ grammars are loadable."""
    err = _init()
    return err is None and bool(_LANGUAGES)


def _walk_errors(node: Any) -> int:
    """Count ERROR / missing nodes under *node* using an explicit stack."""
    count = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if cur.type == "ERROR" or cur.is_missing:
            count += 1
        # Push all children (not just named) for complete coverage
        if hasattr(cur, "children"):
            for child in cur.children:
                stack.append(child)
    return count


def tsa_parse_source(
    source: bytes,
    *,
    language: str,
    line_offset: int = 0,
    transparent_boolean_macros: frozenset[str] = frozenset(),
    noreturn_macros: frozenset[str] = frozenset(),
    source_macros: frozenset[str] = frozenset(),
) -> Optional[Any]:
    """Parse C/C++ source using our robust parser backend.

    Returns a ``ParsedSource`` (from constraint_ast) on success, or None.
    """
    err = _init()
    if err is not None:
        return None

    parser = _get_parser(language)
    if parser is None:
        return None

    try:
        tree = parser.parse(source)
    except Exception as exc:
        _LOG.debug("Parse failed for lang=%s: %s", language, exc)
        return None

    if tree is None or tree.root_node is None:
        return None

    root = tree.root_node
    error_count = _walk_errors(root)

    from .constraint_ast import ParsedSource

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
    )
