"""Stable engine exports."""

from .critic_decorator import critic
from .cancellation import CancelMode, CancelToken
from .engine import Engine, EngineResult, StepSummary
from .async_engine import AsyncEngine
from .events import EngineEvent, EngineEventType, EventStream
from .hooks import EngineHook, HookContext, ToolHookContext
from ._loop_detector import ToolCallLoopDetector
from .states import (
    ContextConfig,
    ContextTelemetry,
    CriticTrace,
    EngineConfig,
    HandoffTrace,
    RuntimeBudget,
    RuntimeEvent,
    RuntimePhase,
    StepRecord,
)

__all__ = [
    "AsyncEngine",
    "CancelMode",
    "CancelToken",
    "CriticTrace",
    "Engine",
    "EngineConfig",
    "EngineHook",
    "EngineResult",
    "EngineEvent",
    "EngineEventType",
    "EventStream",
    "HandoffTrace",
    "HookContext",
    "ToolHookContext",
    "ToolCallLoopDetector",
    "StepSummary",
    "ContextConfig",
    "ContextTelemetry",
    "RuntimeBudget",
    "RuntimeEvent",
    "RuntimePhase",
    "StepRecord",
]
