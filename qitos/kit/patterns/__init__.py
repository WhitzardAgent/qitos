"""Reusable multi-agent pattern templates for QitOS."""

from .manager_worker import ManagerWorkerConfig, build_manager_worker_system
from .planner_executor import PlannerExecutorConfig, build_planner_executor_system
from .proposer_verifier import ProposerVerifierConfig, build_proposer_verifier_system

__all__ = [
    "ManagerWorkerConfig",
    "build_manager_worker_system",
    "PlannerExecutorConfig",
    "build_planner_executor_system",
    "ProposerVerifierConfig",
    "build_proposer_verifier_system",
]
