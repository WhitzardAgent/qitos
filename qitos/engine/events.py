"""Structured engine events for streaming and observability."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from .states import RuntimePhase


class EngineEventType(str, Enum):
    STEP_START = "step_start"
    STEP_END = "step_end"
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    DECIDE = "decide"
    ACT = "act"
    REDUCE = "reduce"
    CRITIC = "critic"
    CHECK_STOP = "check_stop"
    HANDOFF = "handoff"
    DELEGATE = "delegate"
    FANOUT = "fanout"
    ERROR = "error"
    RUN_START = "run_start"
    RUN_END = "run_end"
    STEP_STREAM = "step_stream"  # Token-level streaming chunk


@dataclass
class EngineEvent:
    """Structured event emitted by Engine/AsyncEngine during execution."""

    event_type: EngineEventType
    step_id: int = 0
    agent_id: Optional[str] = None
    phase: Optional[RuntimePhase] = None
    ok: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event_type": self.event_type.value,
            "step_id": self.step_id,
            "ok": self.ok,
            "ts": self.ts,
        }
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        if self.phase is not None:
            d["phase"] = self.phase.value
        if self.payload:
            d["payload"] = self.payload
        if self.error is not None:
            d["error"] = self.error
        return d


class EventStream:
    """Async-compatible event stream for consuming engine events.

    Usage::

        stream = EventStream()
        engine = Engine(agent, ...)
        # Subscribe before starting
        async for event in stream:
            print(event)

        # Producer side (engine):
        stream.emit(EngineEvent(event_type=EngineEventType.STEP_START, ...))
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Optional[EngineEvent]] = asyncio.Queue()
        self._subscribers: List[asyncio.Queue[Optional[EngineEvent]]] = []
        self._closed = False

    def emit(self, event: EngineEvent) -> None:
        """Emit an event to all subscribers (thread-safe for sync callers)."""
        if self._closed:
            return
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def emit_sync(self, event: EngineEvent) -> None:
        """Emit from a sync context (safe to call from Engine.run)."""
        self.emit(event)

    def close(self) -> None:
        """Signal end of stream."""
        self._closed = True
        for q in self._subscribers:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def __aiter__(self) -> AsyncIterator[EngineEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event

    def subscribe(self) -> asyncio.Queue[Optional[EngineEvent]]:
        """Create a new subscriber queue for fan-out consumption."""
        q: asyncio.Queue[Optional[EngineEvent]] = asyncio.Queue(maxsize=1024)
        self._subscribers.append(q)
        return q


__all__ = ["EngineEvent", "EngineEventType", "EventStream"]
