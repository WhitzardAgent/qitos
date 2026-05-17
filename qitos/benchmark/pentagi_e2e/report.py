"""ScoreReport — structured output from PentAGI e2e scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CriterionScore:
    """Score result for a single criterion."""

    name: str
    passed: bool
    points: float
    required: bool
    detail: str = ""  # Why it passed/failed


@dataclass
class ScoreReport:
    """Aggregated score report for a PentAGI e2e test run."""

    scores: List[CriterionScore] = field(default_factory=list)
    tier: int = 0
    target_name: str = ""

    @property
    def total_points(self) -> float:
        return sum(s.points for s in self.scores)

    @property
    def earned_points(self) -> float:
        return sum(s.points for s in self.scores if s.passed)

    @property
    def pass_rate(self) -> float:
        """Fraction of criteria that passed (0.0 to 1.0)."""
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.passed) / len(self.scores)

    @property
    def required_passed(self) -> bool:
        """Whether all required criteria passed."""
        return all(s.passed for s in self.scores if s.required)

    @property
    def failure_reasons(self) -> List[str]:
        """Names of required criteria that failed."""
        return [s.name for s in self.scores if s.required and not s.passed]

    def tier_passed(self, pass_rate_threshold: float = 1.0) -> bool:
        """Check if the tier passes given a pass-rate threshold.

        A tier passes when:
        1. All required criteria pass
        2. Overall pass rate meets the threshold
        """
        return self.required_passed and self.pass_rate >= pass_rate_threshold

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "target_name": self.target_name,
            "pass_rate": self.pass_rate,
            "required_passed": self.required_passed,
            "total_points": self.total_points,
            "earned_points": self.earned_points,
            "failure_reasons": self.failure_reasons,
            "scores": [
                {
                    "name": s.name,
                    "passed": s.passed,
                    "points": s.points,
                    "required": s.required,
                    "detail": s.detail,
                }
                for s in self.scores
            ],
        }

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"PentAGI E2E Score Report — Tier {self.tier} / {self.target_name}",
            f"Pass rate: {self.pass_rate:.0%} | Required: {'PASS' if self.required_passed else 'FAIL'}",
            f"Points: {self.earned_points}/{self.total_points}",
            "",
        ]
        for s in self.scores:
            status = "PASS" if s.passed else "FAIL"
            req = " [required]" if s.required else ""
            lines.append(f"  [{status}] {s.name}{req}")
            if s.detail:
                lines.append(f"         {s.detail}")
        if self.failure_reasons:
            lines.append(f"\nFailed required: {', '.join(self.failure_reasons)}")
        return "\n".join(lines)


__all__ = ["CriterionScore", "ScoreReport"]
