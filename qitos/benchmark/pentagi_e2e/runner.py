"""PentAGI e2e runner — orchestrates a single e2e test run.

Handles target container lifecycle, PentAGI execution, and scoring.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from .criteria import TierCriterion, TIER_PASS_RATES, get_criteria
from .report import ScoreReport
from .scorer import PentagiE2EScorer
from .target_manager import TargetManager
from .targets import TARGETS, VulnerableTarget

logger = logging.getLogger(__name__)


def run_pentagi_e2e_task(
    *,
    target_name: str,
    tier: int,
    task_description: str,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> ScoreReport:
    """Run a single PentAGI e2e test and return a scored report.

    Parameters
    ----------
    target_name : str
        Name of the target to test against (from TARGETS registry).
    tier : int
        Test tier (1=smoke, 2=recon, 3=vuln_discovery, 4=exploit).
    task_description : str
        Task description to pass to PentAGI.
    config_overrides : dict | None
        Additional PentAGIConfig fields to override.

    Returns
    -------
    ScoreReport
        Structured score report with per-criterion results.
    """
    from qitos.examples.pentagi.config.defaults import PentAGIConfig
    from qitos.examples.pentagi.runner import PentAGIRunner

    target = TARGETS[target_name]
    criteria = get_criteria(tier, target_name)
    if not criteria:
        return ScoreReport(tier=tier, target_name=target_name, scores=[])

    manager = TargetManager(target)
    pass_rate_threshold = TIER_PASS_RATES.get(tier, 1.0)

    try:
        # Start target container
        address = manager.start()
        logger.info(f"Target {target_name} started at {address}")

        # Collect ground truth
        ground_truth = manager.get_ground_truth()

        # Tier 4: plant flag
        if tier >= 4:
            flag = f"PENTAGI_FLAG_{uuid4().hex[:12]}"
            manager.plant_flag("/tmp/pentagi_flag.txt", flag, flag_name="exploit_flag")

        # Build PentAGIConfig
        tier_budgets = {
            1: {"max_subtasks": 2, "max_steps_per_subtask": 5, "max_total_steps": 15},
            2: {"max_subtasks": 3, "max_steps_per_subtask": 8, "max_total_steps": 30},
            3: {"max_subtasks": 5, "max_steps_per_subtask": 10, "max_total_steps": 50},
            4: {"max_subtasks": 5, "max_steps_per_subtask": 15, "max_total_steps": 60},
        }
        budget = tier_budgets.get(tier, tier_budgets[2])

        config_params: Dict[str, Any] = {
            "model_provider": os.getenv("PENTAGI_MODEL_PROVIDER", "openai-compatible"),
            "model_name": os.getenv(
                f"PENTAGI_TIER{tier}_MODEL",
                os.getenv("PENTAGI_MODEL_NAME", "gpt-4o-mini"),
            ),
            "api_key": os.getenv("PENTAGI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            "base_url": os.getenv("PENTAGI_BASE_URL"),
            "docker_profile": "kali",
            "authorized_targets": [address],
            "language": "en",
            "temperature": 0.3,
            "ask_user_enabled": False,
            **budget,
        }
        if config_overrides:
            config_params.update(config_overrides)

        config = PentAGIConfig(**config_params)

        # Run PentAGI
        started = time.time()
        runner = PentAGIRunner(config)
        result = runner.run_with_docker(task_description)
        elapsed = time.time() - started

        logger.info(
            f"PentAGI completed in {elapsed:.1f}s — "
            f"status={getattr(result, 'status', '?')}, "
            f"steps={getattr(result, 'total_steps', 0)}"
        )

        # Score results
        scorer = PentagiE2EScorer()
        report = scorer.score(
            result, criteria, ground_truth, manager,
            tier=tier, target_name=target_name,
        )
        return report

    except Exception as e:
        logger.error(f"PentAGI e2e run failed: {e}")
        # Return a failed report
        return ScoreReport(
            tier=tier,
            target_name=target_name,
            scores=[
                CriterionScore(
                    name="run_error",
                    passed=False,
                    points=0.0,
                    required=True,
                    detail=str(e),
                )
                for _ in criteria
            ] or [CriterionScore(
                name="run_error",
                passed=False,
                points=0.0,
                required=True,
                detail=str(e),
            )],
        )

    finally:
        manager.stop()


# Need CriterionScore import for error case
from .report import CriterionScore

__all__ = ["run_pentagi_e2e_task"]
