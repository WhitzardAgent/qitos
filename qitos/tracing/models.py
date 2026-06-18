"""Core data models for the hierarchical tracing system.

Hierarchy:  Trace  →  Span  →  SpanData

- *SpanData* is a typed, self-describing payload (abstract base).
- *Span* wraps a SpanData with timing, IDs, and nesting information.
- *Trace* is a collection of spans produced by a single logical run.

NoOp variants (NoOpSpan, NoOpTrace) allow callers to avoid None checks
when tracing is disabled.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Context variable for async-safe current-span tracking
# ---------------------------------------------------------------------------

_current_span: ContextVar[Optional["Span"]] = ContextVar(
    "_current_span", default=None
)


# ---------------------------------------------------------------------------
# SpanData  (abstract base + concrete subclasses)
# ---------------------------------------------------------------------------


class SpanData(ABC):
    """Abstract base for typed span payloads.

    Each subclass must declare a unique ``type`` string and implement
    ``export()`` which returns a JSON-serializable dictionary.
    """

    @property
    @abstractmethod
    def type(self) -> str:  # noqa: A003 – intentional shadow for clarity
        """Return the span type identifier (e.g. 'agent', 'tool')."""

    @abstractmethod
    def export(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this payload."""


class SpanType(str, Enum):
    """Enumeration of known span types."""

    AGENT = "agent"
    STEP = "step"
    DECIDE = "decide"
    ACT = "act"
    REDUCE = "reduce"
    CRITIC = "critic"
    TOOL = "tool"
    HANDOFF = "handoff"
    DELEGATE = "delegate"
    FANOUT = "fanout"
    PARSE = "parse"
    GENERATION = "generation"
    GUARDRAIL = "guardrail"
    MCP = "mcp"
    CUSTOM = "custom"


# -- Concrete SpanData types -----------------------------------------------


class AgentSpanData(SpanData):
    """Data for a top-level agent span."""

    def __init__(
        self,
        name: str,
        *,
        model: Optional[str] = None,
        tools: Optional[List[str]] = None,
        output_type: Optional[str] = None,
    ) -> None:
        self.name = name
        self.model = model
        self.tools = tools or []
        self.output_type = output_type

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.AGENT.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "model": self.model,
            "tools": self.tools,
            "output_type": self.output_type,
        }


class StepSpanData(SpanData):
    """Data for a single reasoning step within an agent."""

    def __init__(
        self,
        step_number: int,
        *,
        observation: Optional[Any] = None,
        decision: Optional[Any] = None,
    ) -> None:
        self.step_number = step_number
        self.observation = observation
        self.decision = decision

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.STEP.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "step_number": self.step_number,
            "observation": self.observation,
            "decision": self.decision,
        }


class DecideSpanData(SpanData):
    """Data for a decision-making span."""

    def __init__(
        self,
        *,
        input_content: Optional[Any] = None,
        output_content: Optional[Any] = None,
        model_response: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.input_content = input_content
        self.output_content = output_content
        self.model_response = model_response

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.DECIDE.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "input_content": self.input_content,
            "output_content": self.output_content,
            "model_response": self.model_response,
        }


class ActSpanData(SpanData):
    """Data for an action-execution span."""

    def __init__(
        self,
        *,
        action_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        output_content: Optional[Any] = None,
    ) -> None:
        self.action_name = action_name
        self.tool_args = tool_args
        self.output_content = output_content

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.ACT.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "action_name": self.action_name,
            "tool_args": self.tool_args,
            "output_content": self.output_content,
        }


class ReduceSpanData(SpanData):
    """Data for a reduction / aggregation span."""

    def __init__(
        self,
        *,
        input_count: int = 0,
        output_content: Optional[Any] = None,
    ) -> None:
        self.input_count = input_count
        self.output_content = output_content

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.REDUCE.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "input_count": self.input_count,
            "output_content": self.output_content,
        }


class CriticSpanData(SpanData):
    """Data for a critic / evaluation span."""

    def __init__(
        self,
        *,
        critic_name: Optional[str] = None,
        input_content: Optional[Any] = None,
        output_content: Optional[Any] = None,
        score: Optional[float] = None,
    ) -> None:
        self.critic_name = critic_name
        self.input_content = input_content
        self.output_content = output_content
        self.score = score

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.CRITIC.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "critic_name": self.critic_name,
            "input_content": self.input_content,
            "output_content": self.output_content,
            "score": self.score,
        }


class ToolSpanData(SpanData):
    """Data for a tool invocation span."""

    def __init__(
        self,
        *,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
        output_content: Optional[Any] = None,
    ) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.output_content = output_content

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.TOOL.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "output_content": self.output_content,
        }


class HandoffSpanData(SpanData):
    """Data for an agent handoff span."""

    def __init__(
        self,
        *,
        from_agent: Optional[str] = None,
        to_agent: Optional[str] = None,
        output_content: Optional[Any] = None,
        context_strategy: Optional[str] = None,
        messages_passed: Optional[int] = None,
    ) -> None:
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.output_content = output_content
        self.context_strategy = context_strategy
        self.messages_passed = messages_passed

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.HANDOFF.value

    def export(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "type": self.type,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "output_content": self.output_content,
        }
        if self.context_strategy is not None:
            data["context_strategy"] = self.context_strategy
        if self.messages_passed is not None:
            data["messages_passed"] = self.messages_passed
        return data


class GenerationSpanData(SpanData):
    """Data for an LLM generation span."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        input_content: Optional[Any] = None,
        output_content: Optional[Any] = None,
        model_response: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, int]] = None,
    ) -> None:
        self.model = model
        self.input_content = input_content
        self.output_content = output_content
        self.model_response = model_response
        self.token_usage = token_usage

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.GENERATION.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "model": self.model,
            "input_content": self.input_content,
            "output_content": self.output_content,
            "model_response": self.model_response,
            "token_usage": self.token_usage,
        }


class MCPSpanData(SpanData):
    """Data for a Model Context Protocol span."""

    def __init__(
        self,
        *,
        server_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        output_content: Optional[Any] = None,
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.output_content = output_content

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.MCP.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "output_content": self.output_content,
        }


class CustomSpanData(SpanData):
    """User-defined span data with arbitrary payload."""

    def __init__(
        self,
        *,
        name: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.data = data or {}

    @property
    def type(self) -> str:  # noqa: A003
        return SpanType.CUSTOM.value

    def export(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "data": self.data,
        }


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Span:
    """A single timed operation within a trace.

    Spans form a tree via *parent_span_id* and the *contextvars*-
    backed ``_current_span`` token, which allows nested ``create_span``
    calls to automatically determine the parent.
    """

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        data: SpanData,
        parent_span_id: Optional[str] = None,
        processor: Optional[Any] = None,
    ) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.data = data
        self.started_at: Optional[str] = None
        self.ended_at: Optional[str] = None
        self.error: Optional[str] = None
        self.output: Optional[Any] = None
        self._processor = processor
        self._token: Optional[Token[Optional["Span"]]] = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> "Span":
        """Mark the span as started, record the timestamp, and notify the
        processor.  Also push this span onto the contextvar stack."""
        self.started_at = _utcnow_iso()
        self._token = _current_span.set(self)
        if self._processor is not None:
            self._processor.on_span_start(self)
        return self

    def finish(self, error: Optional[str] = None) -> None:
        """Mark the span as finished and notify the processor.

        Restores the previous span from the contextvar stack.
        """
        self.ended_at = _utcnow_iso()
        if error is not None:
            self.error = error
        # Pop contextvar
        if self._token is not None:
            _current_span.reset(self._token)
            self._token = None
        if self._processor is not None:
            self._processor.on_span_end(self)

    def set_output(self, output: Any) -> None:
        """Set the output of this span."""
        self.output = output

    # -- serialization ------------------------------------------------------

    def export(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this span."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "data": self.data.export(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "output": self.output,
        }

    # -- dunder helpers -----------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"Span(id={self.span_id!r}, trace={self.trace_id!r}, "
            f"type={self.data.type!r})"
        )


class NoOpSpan:
    """Sentinel span that discards all operations.

    Returned when tracing is disabled so that callers never need to
    check for None.
    """

    trace_id: str = ""
    span_id: str = ""
    parent_span_id: Optional[str] = None
    data: Optional[SpanData] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None
    output: Optional[Any] = None

    def start(self) -> "NoOpSpan":
        return self

    def finish(self, error: Optional[str] = None) -> None:
        pass

    def set_output(self, output: Any) -> None:
        pass

    def export(self) -> Dict[str, Any]:
        return {}

    def __repr__(self) -> str:
        return "NoOpSpan()"


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class Trace:
    """A logical grouping of spans produced by a single run.

    Usage::

        with provider.create_trace("my-run") as trace:
            span = trace.create_span(SpanType.STEP, StepSpanData(1))
            span.start()
            ...
            span.finish()
    """

    def __init__(
        self,
        trace_id: str,
        name: str,
        group_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        processor: Optional[Any] = None,
    ) -> None:
        self.trace_id = trace_id
        self.name = name
        self.group_id = group_id
        self.metadata = metadata or {}
        self._spans: List[Span] = []
        self._processor = processor

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "Trace":
        if self._processor is not None:
            self._processor.on_trace_start(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        if self._processor is not None:
            self._processor.on_trace_end(self)

    # -- span factory -------------------------------------------------------

    def create_span(
        self,
        span_type: SpanType,
        data: SpanData,
        span_id: Optional[str] = None,
    ) -> Span:
        """Create a new child span under this trace.

        The parent span is automatically determined from the
        ``_current_span`` contextvar if one is active.
        """
        if span_id is None:
            span_id = str(uuid.uuid4())

        # Determine parent from contextvar
        current = _current_span.get()
        parent_span_id: Optional[str] = None
        if current is not None and not isinstance(current, NoOpSpan):
            parent_span_id = current.span_id

        span = Span(
            trace_id=self.trace_id,
            span_id=span_id,
            data=data,
            parent_span_id=parent_span_id,
            processor=self._processor,
        )
        self._spans.append(span)
        return span

    # -- serialization ------------------------------------------------------

    def export(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this trace and
        all its spans."""
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "group_id": self.group_id,
            "metadata": self.metadata,
            "spans": [s.export() for s in self._spans],
        }

    @property
    def spans(self) -> List[Span]:
        return list(self._spans)

    def __repr__(self) -> str:
        return f"Trace(id={self.trace_id!r}, name={self.name!r}, spans={len(self._spans)})"


class NoOpTrace:
    """Sentinel trace that discards all operations.

    Returned when tracing is disabled so that callers never need to
    check for None.
    """

    trace_id: str = ""
    name: str = ""
    group_id: Optional[str] = None
    metadata: Dict[str, Any] = {}

    def __enter__(self) -> "NoOpTrace":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        pass

    def create_span(
        self,
        span_type: SpanType,
        data: SpanData,
        span_id: Optional[str] = None,
    ) -> NoOpSpan:
        return NoOpSpan()

    def export(self) -> Dict[str, Any]:
        return {}

    @property
    def spans(self) -> list:  # type: ignore[type-arg]
        return []

    def __repr__(self) -> str:
        return "NoOpTrace()"
