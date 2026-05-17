"""PentAGI end-to-end benchmark — penetration testing effectiveness evaluation."""

from .targets import VulnerableTarget, TARGETS, get_targets_for_tier
from .criteria import TierCriterion, get_criteria, TIER_PASS_RATES
from .target_manager import TargetManager
from .scorer import PentagiE2EScorer
from .report import CriterionScore, ScoreReport
from .runner import run_pentagi_e2e_task

__all__ = [
    "VulnerableTarget",
    "TARGETS",
    "get_targets_for_tier",
    "TierCriterion",
    "get_criteria",
    "TIER_PASS_RATES",
    "TargetManager",
    "PentagiE2EScorer",
    "CriterionScore",
    "ScoreReport",
    "run_pentagi_e2e_task",
]
