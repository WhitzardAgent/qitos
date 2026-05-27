"""QitOS hierarchical tracing system.

Public API
----------
- ``set_trace_processors`` / ``add_trace_processor`` — configure processors
- ``set_tracing_disabled`` / ``set_tracing_mode`` — control tracing mode
- ``get_tracing_provider`` — access the global provider
- ``Trace``, ``Span``, ``SpanData``, ``SpanType`` — core models
- ``TraceProcessor`` — processor protocol
- ``TracingMode`` — ENABLED / ENABLED_WITHOUT_DATA / DISABLED
- ``WandbTraceProcessor`` — W&B integration (requires ``qitos[wandb]``)
"""

from __future__ import annotations

from typing import List, Optional

from .config import TracingMode
from .models import Span, SpanData, SpanType, Trace
from .processor import TraceProcessor
from .provider import TracingProvider
from .legacy_processor import LegacyTraceWriterProcessor

# Optional W&B processor — only available when wandb is installed
try:
    from .wandb_processor import WandbTraceProcessor  # noqa: F401
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Global provider singleton
# ---------------------------------------------------------------------------

_global_provider = TracingProvider()


def set_trace_processors(processors: List[TraceProcessor]) -> None:
    """Replace the list of trace processors on the global provider."""
    _global_provider.set_processors(processors)


def add_trace_processor(processor: TraceProcessor) -> None:
    """Append a trace processor to the global provider."""
    _global_provider.add_processor(processor)


def set_tracing_disabled(disabled: bool) -> None:
    """Convenience: enable or disable tracing entirely."""
    if disabled:
        _global_provider.set_mode(TracingMode.DISABLED)
    else:
        # When re-enabling, default to full tracing (not WITHOUT_DATA)
        _global_provider.set_mode(TracingMode.ENABLED)


def set_tracing_mode(mode: TracingMode) -> None:
    """Set the tracing mode on the global provider."""
    _global_provider.set_mode(mode)


def get_tracing_provider() -> TracingProvider:
    """Return the global TracingProvider instance."""
    return _global_provider


def create_trace(
    name: str,
    group_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Trace:
    """Create a new trace via the global provider."""
    return _global_provider.create_trace(name, group_id=group_id, metadata=metadata)


__all__ = [
    # models
    "Trace",
    "Span",
    "SpanData",
    "SpanType",
    # processor
    "TraceProcessor",
    # config
    "TracingMode",
    # provider
    "TracingProvider",
    # legacy bridge
    "LegacyTraceWriterProcessor",
    # global helpers
    "set_trace_processors",
    "add_trace_processor",
    "set_tracing_disabled",
    "set_tracing_mode",
    "get_tracing_provider",
    "create_trace",
    # optional integrations
    "WandbTraceProcessor",
]
