"""Bounded Tree-sitter C/C++ interprocedural analysis."""

from .models import *  # noqa: F401,F403
from .service import AnalysisService, AnalysisConfig

__all__ = ["AnalysisService", "AnalysisConfig"]

# Lazy imports for absorbed TSA modules — avoid circular imports at package load.
def __getattr__(name: str):
    _LAZY = {
        "LanguageLoader": ".language_loader",
        "Parser": ".parser",
        "FunctionRef": ".call_graph",
        "CallGraph": ".call_graph",
        "CallPathFinder": ".call_path",
        "CalleeResolver": ".callee_resolution",
        "walk_tree": ".function_extraction",
    }
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
