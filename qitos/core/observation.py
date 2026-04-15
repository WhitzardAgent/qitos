"""Canonical observation contract passed to Agent.reduce()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .tool_result import ToolResult


@dataclass
class Observation(dict):
    """Normalized per-step observation payload."""

    step_id: int
    task: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    decision: Dict[str, Any] | None = None
    action_results: List[ToolResult] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._sync_mapping()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "step": self.step_id,
            "state": dict(self.state),
            "decision": dict(self.decision or {}),
            "action_results": [item.to_dict() for item in self.action_results],
            "env": dict(self.env),
            "metadata": dict(self.metadata),
        }

    def _sync_mapping(self) -> None:
        self.clear()
        self.update(self.to_dict())

    @classmethod
    def from_value(cls, payload: Any) -> "Observation":
        if isinstance(payload, Observation):
            return payload
        if isinstance(payload, dict):
            action_results = [
                ToolResult.from_value(item)
                for item in list(payload.get("action_results", []) or [])
            ]
            decision = payload.get("decision")
            return cls(
                step_id=int(payload.get("step", payload.get("step_id", 0)) or 0),
                task=str(payload.get("task", "") or ""),
                state=(
                    dict(payload.get("state", {}))
                    if isinstance(payload.get("state"), dict)
                    else {}
                ),
                decision=(dict(decision) if isinstance(decision, dict) else None),
                action_results=action_results,
                env=(
                    dict(payload.get("env", {}))
                    if isinstance(payload.get("env"), dict)
                    else {}
                ),
                metadata=(
                    dict(payload.get("metadata", {}))
                    if isinstance(payload.get("metadata"), dict)
                    else {}
                ),
            )
        return cls(step_id=0, metadata={"raw": payload})


__all__ = ["Observation"]
