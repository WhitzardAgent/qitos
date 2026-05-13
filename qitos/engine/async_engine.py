"""Async Engine for non-blocking AgentModule execution."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Generic, List, Optional, TypeVar

from ..core.agent_module import AgentModule
from ..core.decision import Decision
from ..core.errors import StopReason
from ..core.state import StateSchema
from .engine import Engine, EngineResult
from .events import EngineEvent, EngineEventType, EventStream
from .states import ContextConfig, RuntimeBudget, RuntimePhase, StepRecord

StateT = TypeVar("StateT", bound=StateSchema)
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


class AsyncEngine(Generic[StateT, ObservationT, ActionT]):
    """Async execution kernel for AgentModule workflows.

    Provides the same interface as Engine but with async execution:
    - ``await async_engine.arun(task)`` returns EngineResult
    - ``async for event in async_engine.arun_stream(task)`` yields EngineEvents

    Internally delegates to a sync Engine but runs blocking calls in a
    thread pool, and optionally uses Model.acall() when available.
    """

    def __init__(
        self,
        agent: AgentModule[StateT, ObservationT, ActionT],
        **engine_kwargs: Any,
    ):
        self._engine = Engine(agent=agent, **engine_kwargs)
        self.agent = self._engine.agent
        self.event_stream: Optional[EventStream] = None

    @property
    def engine(self) -> Engine[StateT, ObservationT, ActionT]:
        return self._engine

    async def arun(self, task: str, **kwargs: Any) -> EngineResult[StateT]:
        """Run the agent loop asynchronously, returning the final result.

        This executes the sync Engine.run() in a thread pool to avoid
        blocking the event loop.
        """
        return await asyncio.to_thread(self._engine.run, task, **kwargs)

    async def arun_stream(
        self, task: str, **kwargs: Any
    ) -> AsyncIterator[EngineEvent]:
        """Run the agent loop and yield structured events as they occur.

        A sync Engine runs in a thread pool. Engine hooks bridge events
        into the EventStream for async consumption.
        """
        stream = EventStream()
        self.event_stream = stream

        hook = _StreamBridgeHook(stream)
        self._engine.hooks.append(hook)

        def _run() -> EngineResult[StateT]:
            try:
                return self._engine.run(task, **kwargs)
            finally:
                stream.close()

        run_task = asyncio.ensure_future(asyncio.to_thread(_run))

        try:
            async for event in stream:
                yield event
        finally:
            self._engine.hooks = [
                h for h in self._engine.hooks if h is not hook
            ]
            self.event_stream = None
            if not run_task.done():
                run_task.cancel()
                try:
                    await run_task
                except (asyncio.CancelledError, Exception):
                    pass

    def run(self, task: str, **kwargs: Any) -> EngineResult[StateT]:
        """Synchronous run (delegates to underlying Engine)."""
        return self._engine.run(task, **kwargs)


class _StreamBridgeHook:
    """Engine hook that bridges RuntimeEvents into an EventStream."""

    def __init__(self, stream: EventStream) -> None:
        self._stream = stream

    def on_run_start(self, task: str, state: Any, engine: Any) -> None:
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.RUN_START,
                payload={"task": task},
            )
        )

    def on_run_end(self, result: Any, engine: Any) -> None:
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.RUN_END,
                step_id=result.step_count,
                payload={
                    "step_count": result.step_count,
                    "runtime_seconds": result.runtime_seconds,
                    "total_tokens": result.total_tokens,
                    "stop_reason": result.state.stop_reason
                    if result.state
                    else None,
                },
            )
        )

    def on_before_step(self, ctx: Any, engine: Any) -> None:
        agent_id = getattr(ctx.record, "agent_id", None) if ctx.record else None
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.STEP_START,
                step_id=ctx.step_id,
                agent_id=agent_id,
                phase=ctx.phase,
                payload={"phase": ctx.phase.value if ctx.phase else None},
            )
        )

    def on_after_step(self, ctx: Any, engine: Any) -> None:
        agent_id = getattr(ctx.record, "agent_id", None) if ctx.record else None
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.STEP_END,
                step_id=ctx.step_id,
                agent_id=agent_id,
                phase=ctx.phase,
                payload={
                    "phase": ctx.phase.value if ctx.phase else None,
                    "stop_reason": ctx.stop_reason,
                },
            )
        )

    def on_before_decide(self, ctx: Any, engine: Any) -> None:
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.DECIDE,
                step_id=ctx.step_id,
                phase=RuntimePhase.DECIDE,
                payload={"stage": "start"},
            )
        )

    def on_after_decide(self, ctx: Any, engine: Any) -> None:
        mode = None
        if ctx.decision is not None:
            mode = getattr(ctx.decision, "mode", None)
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.DECIDE,
                step_id=ctx.step_id,
                phase=RuntimePhase.DECIDE,
                payload={"stage": "end", "mode": mode},
            )
        )

    def on_before_act(self, ctx: Any, engine: Any) -> None:
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.ACT,
                step_id=ctx.step_id,
                phase=RuntimePhase.ACT,
                payload={"stage": "start"},
            )
        )

    def on_after_act(self, ctx: Any, engine: Any) -> None:
        self._stream.emit_sync(
            EngineEvent(
                event_type=EngineEventType.ACT,
                step_id=ctx.step_id,
                phase=RuntimePhase.ACT,
                payload={"stage": "end"},
            )
        )

    def on_event(
        self, event: Any, state: Any, record: Any, engine: Any
    ) -> None:
        phase_val = getattr(event, "phase", None)
        phase_str = phase_val.value if phase_val else ""
        event_type = EngineEventType.PHASE_END

        if "HANDOFF" in phase_str:
            event_type = EngineEventType.HANDOFF
        elif "DELEGATE" in phase_str:
            event_type = EngineEventType.DELEGATE
        elif "FANOUT" in phase_str:
            event_type = EngineEventType.FANOUT

        agent_id = getattr(record, "agent_id", None) if record else None
        self._stream.emit_sync(
            EngineEvent(
                event_type=event_type,
                step_id=getattr(event, "step_id", 0),
                agent_id=agent_id,
                phase=phase_val,
                ok=getattr(event, "ok", True),
                payload=getattr(event, "payload", {}) or {},
                error=getattr(event, "error", None),
            )
        )


__all__ = ["AsyncEngine"]
