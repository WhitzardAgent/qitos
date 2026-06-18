"""ToolInterceptor protocol for before/after hooks around tool execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .action import Action, ActionResult


@dataclass
class InterceptorContext:
    """Context passed to interceptor hooks during tool execution."""

    tool_name: str
    tool_args: Dict[str, Any]
    step_id: int
    state: Any = None
    run_id: str = ""


class ToolInterceptor(ABC):
    """Abstract base class for tool execution interceptors.

    Subclass this to add before/after hooks around tool execution.
    """

    @abstractmethod
    def before_execute(self, action: Action, context: InterceptorContext) -> Action:
        """Called before a tool is executed. May modify and return the action.

        Args:
            action: The action about to be executed.
            context: Context describing the tool call.

        Returns:
            The (possibly modified) action to execute.
        """

    @abstractmethod
    def after_execute(
        self, action: Action, result: ActionResult, context: InterceptorContext
    ) -> ActionResult:
        """Called after a tool is executed. May modify and return the result.

        Args:
            action: The action that was executed.
            result: The result from tool execution.
            context: Context describing the tool call.

        Returns:
            The (possibly modified) result.
        """


class InterceptorChain:
    """Runs a list of ToolInterceptor instances in order.

    before_execute is called in list order (first added, first called).
    after_execute is called in reverse order (last added, first called),
    so the chain unwinds like a stack.
    """

    def __init__(self, interceptors: Optional[List[ToolInterceptor]] = None):
        self.interceptors: List[ToolInterceptor] = list(interceptors or [])

    def add(self, interceptor: ToolInterceptor) -> None:
        """Append an interceptor to the chain."""
        self.interceptors.append(interceptor)

    def before_execute(self, action: Action, context: InterceptorContext) -> Action:
        """Run all interceptors' before_execute in order."""
        for interceptor in self.interceptors:
            action = interceptor.before_execute(action, context)
        return action

    def after_execute(
        self, action: Action, result: ActionResult, context: InterceptorContext
    ) -> ActionResult:
        """Run all interceptors' after_execute in reverse order."""
        for interceptor in reversed(self.interceptors):
            result = interceptor.after_execute(action, result, context)
        return result


__all__ = [
    "InterceptorChain",
    "InterceptorContext",
    "ToolInterceptor",
]
