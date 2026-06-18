"""@critic decorator — convert functions to Critic instances.

Usage::

    @critic
    def check_errors(state, decision, results):
        if any_error(results):
            return "stop", "errors found"
        return "continue"

    @critic(name="safety", score=0.8)
    def safety_check(state, decision, results):
        if unsafe(results):
            return "retry", "unsafe output", "Be more careful"
        return "continue"

Quick return values:
    - ``return "continue"`` — proceed normally
    - ``return "stop", "reason"`` — halt execution
    - ``return "retry", "reason"`` — retry with same prompt
    - ``return "retry", "reason", instruction_patch`` — retry with appended instruction
    - ``return CriticResult(...)`` — full structured result (also accepted)
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional, TypeVar, overload

from .critic import Critic
from .critic_result import CriticResult

F = TypeVar("F", bound=Callable)


def _coerce_return(value: Any) -> CriticResult:
    """Convert quick-return shorthand to CriticResult."""
    if isinstance(value, CriticResult):
        return value
    if isinstance(value, dict):
        return CriticResult.from_dict(value)
    if isinstance(value, str):
        return CriticResult(action=value)
    if isinstance(value, tuple):
        action = value[0] if len(value) > 0 else "continue"
        reason = value[1] if len(value) > 1 else ""
        instruction_patch = value[2] if len(value) > 2 else None
        return CriticResult(
            action=action,
            reason=reason,
            instruction_patch=instruction_patch,
        )
    return CriticResult(action="continue")


class _FunctionCritic(Critic):
    """Critic backed by a plain function."""

    def __init__(
        self,
        func: Callable,
        name: Optional[str] = None,
        score: float = 1.0,
    ):
        self._func = func
        self._name = name or getattr(func, "__name__", "critic")
        self._default_score = score
        functools.update_wrapper(self, func, updated=())  # type: ignore[arg-type]

    def evaluate(
        self, state: Any, decision: Any, results: list[Any]
    ) -> CriticResult:
        raw = self._func(state, decision, results)
        result = _coerce_return(raw)
        # Apply default score only if caller didn't set one explicitly
        if result.score == 1.0 and self._default_score != 1.0:
            result.score = self._default_score
        return result

    def __repr__(self) -> str:
        return f"_FunctionCritic({self._name!r})"


@overload
def critic(
    __func_or_none__: None = None,
    *,
    name: Optional[str] = None,
    score: float = 1.0,
) -> Callable[[F], _FunctionCritic]:
    ...


@overload
def critic(
    __func_or_none__: F,
    *,
    name: Optional[str] = None,
    score: float = 1.0,
) -> _FunctionCritic:
    ...


def critic(
    __func_or_none__: Any = None,
    *,
    name: Optional[str] = None,
    score: float = 1.0,
) -> Any:
    """Decorator to convert a function into a Critic instance.

    Can be used bare (``@critic``) or with keyword arguments
    (``@critic(name="safety", score=0.8)``).

    Parameters
    ----------
    name : str, optional
        Name for the critic (defaults to function name).
    score : float
        Default quality score applied when the function doesn't
        return one explicitly.
    """

    def decorator(func: F) -> _FunctionCritic:
        return _FunctionCritic(func, name=name, score=score)

    if __func_or_none__ is not None:
        # Bare usage: @critic
        return decorator(__func_or_none__)

    # With arguments: @critic(name=..., score=...)
    return decorator


__all__ = ["critic", "_FunctionCritic", "_coerce_return"]
