"""Search backends for web and information retrieval."""

from .base import SearchBackend, SearchResult
from .duckduckgo import DuckDuckGoSearchBackend
from .searxng import SearXNGSearchBackend
from .tavily import TavilySearchBackend
from .google_cse import GoogleCSESearchBackend
from .traversaal import TraversaalSearchBackend
from .perplexity import PerplexitySearchBackend
from .sploitus import SploitusSearchBackend

__all__ = [
    "SearchBackend",
    "SearchResult",
    "DuckDuckGoSearchBackend",
    "SearXNGSearchBackend",
    "TavilySearchBackend",
    "GoogleCSESearchBackend",
    "TraversaalSearchBackend",
    "PerplexitySearchBackend",
    "SploitusSearchBackend",
]
