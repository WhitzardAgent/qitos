"""Critic abstraction for verifier-guided runtime loops."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Union

from ..core.decision import Decision
from .critic_result import CriticResult


class Critic(ABC):
    @abstractmethod
    def evaluate(
        self, state: Any, decision: Decision[Any], results: list[Any]
    ) -> Union[CriticResult, Dict[str, Any]]:
        """Return a structured critic decision.

        May return a ``CriticResult`` (preferred) or a plain dict (legacy).

        Supported keys (by convention / CriticResult fields):
        - action: "continue" | "stop" | "retry"
        - reason: str
        - score: float
        - details: dict
        - modified_prompt: str | None  (used when action="retry")
        - instruction_patch: str | None  (appended to system prompt on retry)
        - state_patch: dict | None  (merged into agent state on retry)
        """


__all__ = ["Critic"]
