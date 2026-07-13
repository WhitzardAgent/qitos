"""Unified error taxonomy for QitOS runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class ErrorCategory(str, Enum):
    MODEL = "model_error"
    PARSE = "parse_error"
    TOOL = "tool_error"
    STATE = "state_error"
    TASK = "task_error"
    ENV = "env_error"
    SYSTEM = "system_error"


class StopReason(str, Enum):
    SUCCESS = "success"
    FINAL = "final"
    MAX_STEPS = "max_steps"
    BUDGET_STEPS = "budget_steps"
    BUDGET_TIME = "budget_time"
    BUDGET_TOKENS = "budget_tokens"
    CONTEXT_OVERFLOW = "context_overflow"
    AGENT_CONDITION = "agent_condition"
    CRITIC_STOP = "critic_stop"
    STAGNATION = "stagnation"
    ENV_TERMINAL = "env_terminal"
    TASK_VALIDATION_FAILED = "task_validation_failed"
    ENV_CAPABILITY_MISMATCH = "env_capability_mismatch"
    INFRASTRUCTURE_INVALID = "infrastructure_invalid"
    UNRECOVERABLE_ERROR = "unrecoverable_error"
    INTERRUPT = "interrupt"


@dataclass
class RuntimeErrorInfo:
    category: ErrorCategory
    message: str
    phase: str
    step_id: int
    recoverable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class QitosRuntimeError(Exception):
    def __init__(self, info: RuntimeErrorInfo):
        super().__init__(f"[{info.category.value}] {info.message}")
        self.info = info


class ModelExecutionError(QitosRuntimeError):
    pass


class ParseExecutionError(QitosRuntimeError):
    pass


class ToolExecutionError(QitosRuntimeError):
    pass


class StateExecutionError(QitosRuntimeError):
    pass


class SystemExecutionError(QitosRuntimeError):
    pass


def _is_network_error(exc: Exception) -> bool:
    """Check if an exception is a transient network/SSL error."""
    # Standard Python errors
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    # httpx transport errors (SSL, connection reset, etc.)
    try:
        import httpx
        if isinstance(exc, (httpx.ConnectError, httpx.TransportError, httpx.TimeoutException)):
            return True
    except ImportError:
        pass
    # openai API errors
    try:
        import openai
        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
    except ImportError:
        pass
    # Check for SSL-related messages as fallback
    msg = str(exc).lower()
    return any(kw in msg for kw in ("ssl", "eof", "connection reset", "connection refused", "timed out"))


def classify_exception(exc: Exception, phase: str, step_id: int) -> RuntimeErrorInfo:
    if isinstance(exc, ModelExecutionError):
        return exc.info
    if isinstance(exc, ParseExecutionError):
        return exc.info
    if isinstance(exc, ToolExecutionError):
        return exc.info
    if isinstance(exc, StateExecutionError):
        return exc.info
    if isinstance(exc, SystemExecutionError):
        return exc.info

    msg = str(exc).lower()

    # Context length / prompt too long errors from API providers
    if any(kw in msg for kw in (
        "context_length_exceeded", "context length", "prompt too long",
        "maximum context", "too many tokens", "reduce the length",
        "input is too long",
    )):
        from .errors import ErrorCategory as _EC
        return RuntimeErrorInfo(
            category=_EC.MODEL,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
            details={"api_context_overflow": True},
        )

    # Network/SSL/connection errors are recoverable in decide phase
    if _is_network_error(exc) and phase.lower() in {"decide", "propose"}:
        return RuntimeErrorInfo(
            category=ErrorCategory.MODEL,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
        )

    # Transient model-call failures in model-driven phases are recoverable —
    # retry the step instead of killing the run. GLM/SGLang stream stalls and
    # gateway outages surface as openai.APIError("stream timeout") /
    # "no healthy upstream", which are NEITHER a TimeoutError NOR match the
    # "timed out" keyword above, so they must be matched by substring here.
    # (Restores pre-v06 behaviour; see tests/test_runtime_recovery.py. Without
    # this, a single GLM stream timeout in DECIDE was classified SYSTEM /
    # non-recoverable -> immediate unrecoverable_error, killing ~50% of tasks.)
    if phase.lower() in {
        "decide",
        "propose",
        "plan",
        "formulation",
        "investigation",
    } and any(
        marker in msg
        for marker in (
            "timeout",
            "timed out",
            "stream timeout",
            "read timeout",
            "connection error",
            "api connection",
            "no healthy upstream",
            "service unavailable",
            "bad gateway",
            "overloaded",
        )
    ):
        return RuntimeErrorInfo(
            category=ErrorCategory.MODEL,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
        )

    if isinstance(exc, (TimeoutError, ConnectionError)) and phase.lower() in {
        "decide",
        "propose",
    }:
        return RuntimeErrorInfo(
            category=ErrorCategory.MODEL,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
        )

    if isinstance(exc, ValueError) and (
        "decision mode" in msg or "parser" in msg or "json" in msg or "xml" in msg
    ):
        return RuntimeErrorInfo(
            category=ErrorCategory.PARSE,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
        )

    if isinstance(exc, (TypeError, AttributeError, AssertionError)) and "state" in msg:
        return RuntimeErrorInfo(
            category=ErrorCategory.STATE,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=False,
        )

    if phase.upper() == "ACT":
        return RuntimeErrorInfo(
            category=ErrorCategory.TOOL,
            message=str(exc),
            phase=phase,
            step_id=step_id,
            recoverable=True,
        )

    return RuntimeErrorInfo(
        category=ErrorCategory.SYSTEM,
        message=str(exc),
        phase=phase,
        step_id=step_id,
        recoverable=False,
    )
