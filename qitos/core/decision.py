"""Canonical decision contract for the QitOS kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar


ActionT = TypeVar("ActionT")
DecisionMode = Literal["act", "final", "wait", "branch", "handoff"]


@dataclass
class Decision(Generic[ActionT]):
    mode: DecisionMode
    actions: List[ActionT] = field(default_factory=list)
    final_answer: Optional[str] = None
    rationale: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    candidates: List["Decision[ActionT]"] = field(default_factory=list)

    @classmethod
    def act(
        cls,
        actions: List[ActionT],
        rationale: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Decision[ActionT]":
        return cls(mode="act", actions=actions, rationale=rationale, meta=meta or {})

    @classmethod
    def final(
        cls,
        answer: str,
        rationale: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Decision[ActionT]":
        return cls(
            mode="final", final_answer=answer, rationale=rationale, meta=meta or {}
        )

    @classmethod
    def wait(
        cls,
        rationale: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Decision[ActionT]":
        return cls(mode="wait", rationale=rationale, meta=meta or {})

    @classmethod
    def branch(
        cls,
        candidates: List["Decision[ActionT]"],
        rationale: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Decision[ActionT]":
        return cls(
            mode="branch", candidates=candidates, rationale=rationale, meta=meta or {}
        )

    @classmethod
    def handoff(
        cls,
        target: str,
        rationale: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Decision[ActionT]":
        combined_meta = meta or {}
        combined_meta["handoff_target"] = target
        return cls(mode="handoff", rationale=rationale, meta=combined_meta)

    def validate(self) -> None:
        if not isinstance(self.meta, dict):
            raise ValueError("Decision.meta must be a dict")
        if self.mode == "act" and not self.actions:
            raise ValueError("Decision(mode='act') requires non-empty actions")
        if self.mode == "final" and not self.final_answer:
            raise ValueError("Decision(mode='final') requires final_answer")
        if self.mode == "branch" and not self.candidates:
            raise ValueError("Decision(mode='branch') requires candidates")
        if self.mode == "branch":
            for candidate in self.candidates:
                if not isinstance(candidate, Decision):
                    raise ValueError(
                        "Decision(mode='branch') requires Decision candidates"
                    )
                candidate.validate()
        if self.mode == "handoff" and not self.meta.get("handoff_target"):
            raise ValueError("Decision(mode='handoff') requires meta['handoff_target']")
