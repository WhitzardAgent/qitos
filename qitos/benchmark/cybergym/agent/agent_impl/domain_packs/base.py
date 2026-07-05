"""Base helpers for lightweight CyberGym domain packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DomainPackResult:
    pack: str
    match_score: float
    status: str
    recipe_patch: dict[str, Any] = field(default_factory=dict)
    rewrite_plan: dict[str, Any] = field(default_factory=dict)
    open_gaps: list[str] = field(default_factory=list)
    sanity_expectations: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "match_score": round(float(self.match_score), 3),
            "status": self.status,
            "recipe_patch": self.recipe_patch,
            "rewrite_plan": self.rewrite_plan,
            "open_gaps": self.open_gaps,
            "sanity_expectations": self.sanity_expectations,
        }


class DomainPack:
    name = "base"

    def match(self, state: Any) -> float:
        raise NotImplementedError

    def build(self, state: Any) -> DomainPackResult:
        raise NotImplementedError


def state_text(state: Any) -> str:
    parts = [
        getattr(state, "task_id", ""),
        getattr(state, "project_name", ""),
        getattr(state, "vulnerability_description", ""),
        getattr(state, "crash_type", ""),
        getattr(getattr(state, "input_format", None), "format_type", ""),
        getattr(getattr(getattr(state, "input_format", None), "consumption", None), "container_structure", ""),
        getattr(getattr(getattr(state, "input_format", None), "consumption", None), "endpoint_scope", ""),
        getattr(state, "harness_protocols", []),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def result_for_format(
    *,
    pack: str,
    score: float,
    fmt: str,
    operations: list[dict[str, Any]] | None = None,
    gaps: list[str] | None = None,
    sanity: list[dict[str, Any]] | None = None,
) -> DomainPackResult:
    gaps = gaps or []
    status = "ready" if not gaps else "partial"
    return DomainPackResult(
        pack=pack,
        match_score=score,
        status=status,
        recipe_patch={"carrier": {"format": fmt, "seed_policy": "minimal_template_ok"}},
        rewrite_plan={"operations": operations or [], "invariants": [f"preserve_{fmt}_carrier"]},
        open_gaps=gaps,
        sanity_expectations=sanity or [{"kind": "format", "expected": fmt, "description": f"preserve {fmt} carrier"}],
    )
