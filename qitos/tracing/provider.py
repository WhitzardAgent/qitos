"""Tracing provider: factory and registry for traces and processors."""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, cast

from .config import TracingMode, RedactingSpanData
from .models import (
    Span,
    SpanData,
    SpanType,
    Trace,
    NoOpSpan,
    NoOpTrace,
)
from .processor import (
    TraceProcessor,
    SynchronousMultiTraceProcessor,
    NoOpTraceProcessor,
)


class TracingProvider:
    """Central factory and registry for the tracing system.

    Responsibilities:
    - Create ``Trace`` instances (or ``NoOpTrace`` when disabled).
    - Manage the list of ``TraceProcessor`` implementations.
    - Honour ``TracingMode`` (enabled / without-data / disabled).
    """

    def __init__(
        self,
        processors: Optional[List[TraceProcessor]] = None,
        mode: TracingMode = TracingMode.ENABLED,
    ) -> None:
        self._multi_processor = SynchronousMultiTraceProcessor(processors)
        self._mode = mode

    # -- configuration ------------------------------------------------------

    def set_processors(self, processors: List[TraceProcessor]) -> None:
        """Replace the current processor list with *processors*."""
        self._multi_processor = SynchronousMultiTraceProcessor(processors)

    def add_processor(self, processor: TraceProcessor) -> None:
        """Append *processor* to the active processor list."""
        self._multi_processor.add_processor(processor)

    def set_mode(self, mode: TracingMode) -> None:
        """Change the tracing mode."""
        self._mode = mode

    @property
    def mode(self) -> TracingMode:
        return self._mode

    # -- trace factory ------------------------------------------------------

    def create_trace(
        self,
        name: str,
        group_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        trace_id: Optional[str] = None,
    ) -> Trace | NoOpTrace:
        """Create a new trace.

        Returns ``NoOpTrace`` when the mode is ``DISABLED``.
        When the mode is ``ENABLED_WITHOUT_DATA``, span data will be
        wrapped in ``RedactingSpanData`` at export time.
        """
        if self._mode == TracingMode.DISABLED:
            return NoOpTrace()

        if trace_id is None:
            trace_id = self.gen_trace_id()

        processor = self._get_processor()
        return Trace(
            trace_id=trace_id,
            name=name,
            group_id=group_id,
            metadata=metadata,
            processor=processor,
        )

    # -- ID generation ------------------------------------------------------

    @staticmethod
    def gen_trace_id() -> str:
        """Generate a new unique trace ID."""
        return str(uuid.uuid4())

    @staticmethod
    def gen_span_id() -> str:
        """Generate a new unique span ID."""
        return str(uuid.uuid4())

    # -- lifecycle ----------------------------------------------------------

    def shutdown(self) -> None:
        """Shut down all processors."""
        self._multi_processor.shutdown()

    def force_flush(self) -> None:
        """Force-flush all processors."""
        self._multi_processor.force_flush()

    # -- internals ----------------------------------------------------------

    def _get_processor(self) -> TraceProcessor:
        """Return the effective processor.

        When running under ``ENABLED_WITHOUT_DATA``, wraps the
        multi-processor so that span data is redacted on the fly.
        """
        if self._mode == TracingMode.ENABLED_WITHOUT_DATA:
            return _RedactingProcessor(self._multi_processor)
        return self._multi_processor


class _RedactingProcessor(TraceProcessor):
    """Thin proxy that redacts span data before delegating to the real
    processor chain.
    """

    def __init__(self, delegate: TraceProcessor) -> None:
        self._delegate = delegate

    def on_trace_start(self, trace: Trace) -> None:
        self._delegate.on_trace_start(trace)

    def on_trace_end(self, trace: Trace) -> None:
        # Redact before emitting
        self._delegate.on_trace_end(self._redact_trace(trace))

    def on_span_start(self, span: Span) -> None:
        self._delegate.on_span_start(span)

    def on_span_end(self, span: Span) -> None:
        # Create a snapshot span with redacted data so that downstream
        # processors see redacted content even if they hold a reference.
        redacted_span = self._redact_span(span)
        self._delegate.on_span_end(redacted_span)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self) -> None:
        self._delegate.force_flush()

    @staticmethod
    def _redact_span(span: Span) -> Span:
        """Return a snapshot span with redacted data."""
        rs = Span(
            trace_id=span.trace_id,
            span_id=span.span_id,
            data=cast(SpanData, RedactingSpanData(span.data)),
            parent_span_id=span.parent_span_id,
        )
        rs.started_at = span.started_at
        rs.ended_at = span.ended_at
        rs.error = span.error
        rs.output = span.output
        return rs

    @staticmethod
    def _redact_trace(trace: Trace) -> Trace:
        """Return a trace whose spans have redacted data."""
        # We build a lightweight read-only snapshot; we do NOT mutate the
        # original trace object.
        redacted = Trace(
            trace_id=trace.trace_id,
            name=trace.name,
            group_id=trace.group_id,
            metadata=trace.metadata,
        )
        redacted._spans = []
        for span in trace._spans:
            rs = Span(
                trace_id=span.trace_id,
                span_id=span.span_id,
                data=cast(SpanData, RedactingSpanData(span.data)),
                parent_span_id=span.parent_span_id,
            )
            rs.started_at = span.started_at
            rs.ended_at = span.ended_at
            rs.error = span.error
            rs.output = span.output
            redacted._spans.append(rs)
        return redacted
