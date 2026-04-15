"""Canonical history contracts for model message context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class HistoryMessage:
    role: str
    step_id: int
    content: Any = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HistoryPolicy:
    """Engine-side policy for selecting/assembling history messages."""

    roles: List[str] = field(default_factory=lambda: ["user", "assistant", "tool"])
    max_messages: int = 24
    step_window: Optional[int] = None
    max_tokens: Optional[int] = None

    def build_query(self, step_id: int, **kwargs: Any) -> Dict[str, Any]:
        query: Dict[str, Any] = {
            "roles": list(self.roles),
            "max_items": int(self.max_messages),
        }
        if self.step_window is not None and self.step_window > 0:
            query["step_min"] = max(0, int(step_id) - int(self.step_window) + 1)
        if self.max_tokens is not None and int(self.max_tokens) > 0:
            query["max_tokens"] = int(self.max_tokens)
        query.update({str(key): value for key, value in kwargs.items()})
        return query


class History(ABC):
    @abstractmethod
    def append(self, message: HistoryMessage) -> None:
        """Append one chat message into history store."""

    @abstractmethod
    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> Any:
        """Retrieve history payload used for model message assembly."""

    @abstractmethod
    def summarize(self, max_items: int = 5) -> str:
        """Return strategy-specific summary for old messages."""

    @abstractmethod
    def evict(self) -> int:
        """Apply retention strategy and return number of evicted messages."""

    @abstractmethod
    def reset(self, run_id: Optional[str] = None) -> None:
        """Reset history runtime state for a new run."""


__all__ = ["History", "HistoryMessage", "HistoryPolicy"]
