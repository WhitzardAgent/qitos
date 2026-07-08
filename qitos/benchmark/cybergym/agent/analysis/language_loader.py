"""Dynamic Tree-sitter language loader for C and C++.

Absorbed from tree-sitter-analyzer's language_loader.py, trimmed to C/C++
only and adapted for cybergym_agent's import conventions.

Handles multiple tree-sitter Python binding API versions gracefully:
- older: Parser.set_language(Language(capsule))
- mid:   parser.language = Language(capsule)
- newer: Parser(Language(obj))
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from tree_sitter import Language, Parser

_LOG = logging.getLogger(__name__)

try:
    import tree_sitter

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Language registry — C and C++ only (our target domain).
# ---------------------------------------------------------------------------
LANGUAGE_MODULES: dict[str, str] = {
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
}

# File-extension → language mapping for convenience.
EXTENSION_LANGUAGE: dict[str, str] = {
    ".c": "c",
    ".h": "c",       # ambiguous; caller may override to "cpp"
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
}


def _try_wrap_language(caps_or_lang: Any, language: str) -> Any | None:
    """Wrap a PyCapsule in tree_sitter.Language; return None on failure."""
    if not TREE_SITTER_AVAILABLE:
        return None
    try:
        return tree_sitter.Language(caps_or_lang)
    except Exception as exc:
        _LOG.debug("Failed to create Language object for %s: %s", language, exc)
        return None


class LanguageLoader:
    """Tree-sitter language loader with caching for C and C++."""

    def __init__(self) -> None:
        self._loaded_languages: dict[str, Any] = {}
        self._loaded_modules: dict[str, Any] = {}
        self._availability_cache: dict[str, bool] = {}
        self._parser_cache: dict[str, Any] = {}
        self._unavailable_languages: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_language_available(self, language: str) -> bool:
        """Check whether the grammar module for *language* is importable."""
        if language in self._unavailable_languages:
            return False
        if language in self._availability_cache:
            return self._availability_cache[language]
        if not TREE_SITTER_AVAILABLE:
            self._availability_cache[language] = False
            self._unavailable_languages.add(language)
            return False
        module_name = LANGUAGE_MODULES.get(language)
        if not module_name:
            self._availability_cache[language] = False
            self._unavailable_languages.add(language)
            return False
        try:
            importlib.import_module(module_name)
            self._availability_cache[language] = True
            return True
        except ImportError:
            self._availability_cache[language] = False
            self._unavailable_languages.add(language)
            return False

    def is_grammar_installed(self, language: str) -> bool:
        """Fast probe using find_spec (no import side-effects)."""
        module_name = LANGUAGE_MODULES.get(language)
        if module_name is None:
            return False
        return importlib.util.find_spec(module_name) is not None

    # ------------------------------------------------------------------
    # Language loading
    # ------------------------------------------------------------------

    def load_language(self, language: str) -> Any | None:
        """Load and return a tree-sitter Language object for *language*."""
        if not TREE_SITTER_AVAILABLE:
            _LOG.warning("Tree-sitter is not available")
            return None
        if language in self._loaded_languages:
            return self._loaded_languages[language]
        if not self.is_language_available(language):
            return None
        try:
            module_name = LANGUAGE_MODULES[language]
            if module_name not in self._loaded_modules:
                self._loaded_modules[module_name] = importlib.import_module(module_name)
            module = self._loaded_modules[module_name]

            lang_attr = f"language_{language}"
            if hasattr(module, "language"):
                language_func = module.language
            elif hasattr(module, lang_attr):
                language_func = getattr(module, lang_attr)
            else:
                return None

            caps_or_lang = language_func()

            # Newer tree-sitter: language_func() returns a Language directly.
            if hasattr(caps_or_lang, "__class__") and "Language" in str(type(caps_or_lang)):
                tree_sitter_language = caps_or_lang
            else:
                # Older: PyCapsule → wrap in Language.
                tree_sitter_language = _try_wrap_language(caps_or_lang, language)
                if tree_sitter_language is None:
                    return None

            self._loaded_languages[language] = tree_sitter_language
            return tree_sitter_language
        except (ImportError, AttributeError, Exception) as exc:
            _LOG.debug("Failed to load language '%s': %s", language, exc)
            self._unavailable_languages.add(language)
            return None

    # ------------------------------------------------------------------
    # Parser creation
    # ------------------------------------------------------------------

    def create_parser(self, language: str) -> Optional["Parser"]:
        """Create a cached tree-sitter Parser for *language*."""
        if not TREE_SITTER_AVAILABLE:
            _LOG.warning("Tree-sitter is not available")
            return None
        if language in self._parser_cache:
            return self._parser_cache[language]

        tree_sitter_language = self.load_language(language)
        if tree_sitter_language is None:
            return None
        try:
            parser = tree_sitter.Parser()
            bound = self._bind_parser_language(parser, tree_sitter_language, language)
            if bound is None:
                return None
            self._parser_cache[language] = bound
            return bound
        except Exception as exc:
            _LOG.debug("Failed to create parser for '%s': %s", language, exc)
            return None

    @staticmethod
    def _bind_parser_language(
        parser: Any, tree_sitter_language: Any, language: str
    ) -> Any | None:
        """Set the language on *parser* using whichever API the build exposes.

        Tree-sitter Python bindings have shifted across releases:
        - older: ``Parser.set_language(Language)``
        - mid:   writable ``parser.language`` attribute
        - newer: ``Parser(Language)`` constructor
        """
        if hasattr(parser, "set_language"):
            parser.set_language(tree_sitter_language)
            return parser
        if hasattr(parser, "language"):
            parser.language = tree_sitter_language
            return parser
        try:
            return tree_sitter.Parser(tree_sitter_language)
        except Exception as exc:
            _LOG.debug(
                "Failed to create parser with language constructor for %s: %s",
                language,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_supported_languages(self) -> list[str]:
        """Return languages whose grammars are installed."""
        return [
            lang
            for lang in LANGUAGE_MODULES
            if lang not in self._unavailable_languages
            and self.is_language_available(lang)
        ]

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear all internal caches."""
        self._loaded_languages.clear()
        self._loaded_modules.clear()
        self._availability_cache.clear()
        self._parser_cache.clear()
        self._unavailable_languages.clear()

    def tsa_available(self) -> bool:
        """Return True if at least one C/C++ grammar is loadable."""
        return self.is_language_available("c") or self.is_language_available("cpp")


# ---------------------------------------------------------------------------
# Module-level convenience (no singleton — callers instantiate directly)
# ---------------------------------------------------------------------------

def check_language_availability(language: str) -> bool:
    """Quick check using a throwaway loader (no caching)."""
    return importlib.util.find_spec(LANGUAGE_MODULES.get(language, "")) is not None
