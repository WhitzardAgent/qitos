"""Structured result type for Critic evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CriticResult:
    """Structured output from a Critic evaluation.

    Attributes
    ----------
    action : str
        One of ``"continue"``, ``"stop"``, ``"retry"``.
    reason : str
        Human-readable explanation of the critic's decision.
    score : float
        Quality score (0.0–1.0). Higher is better.
    details : dict
        Additional structured information about the evaluation.
    modified_prompt : str | None
        If action is ``"retry"``, an optional replacement system prompt
        to inject into the next iteration.
    instruction_patch : str | None
        If action is ``"retry"``, an optional additional instruction
        appended to the agent's system prompt on the next iteration.
    state_patch : dict | None
        If action is ``"retry"``, optional key-value pairs to merge into
        the agent's state before the next iteration.
    """

    action: str = "continue"
    reason: str = ""
    score: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    modified_prompt: Optional[str] = None
    instruction_patch: Optional[str] = None
    state_patch: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict (backward-compatible with legacy dict returns)."""
        d: Dict[str, Any] = {
            "action": self.action,
            "reason": self.reason,
            "score": self.score,
        }
        if self.details:
            d["details"] = self.details
        if self.modified_prompt is not None:
            d["modified_prompt"] = self.modified_prompt
        if self.instruction_patch is not None:
            d["instruction_patch"] = self.instruction_patch
        if self.state_patch is not None:
            d["state_patch"] = self.state_patch
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CriticResult:
        """Create from a dict (backward-compatible with legacy dict returns)."""
        return cls(
            action=str(d.get("action", "continue")),
            reason=str(d.get("reason", "")),
            score=float(d.get("score", 1.0)),
            details=d.get("details", {}),
            modified_prompt=d.get("modified_prompt"),
            instruction_patch=d.get("instruction_patch"),
            state_patch=d.get("state_patch"),
        )


__all__ = ["CriticResult"]
