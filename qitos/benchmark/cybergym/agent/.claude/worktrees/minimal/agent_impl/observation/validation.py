"""Minimal validation mixin for the Minimal CyberGym Agent.

All tool gating, bash classification, read budgets, and control mode
logic has been removed. The model decides its own workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...state import CyberGymState


class ValidationMixin:
    """Minimal validation — only path resolution utilities.

    No tool gating, no bash command validation, no read budgets,
    no control modes, no candidate requirement forcing.
    The model has full access to all 9 tools at all times.
    """

    @staticmethod
    def _resolve_candidate_path(state: CyberGymState, path: str) -> Path:
        candidate = Path(str(path or ""))
        if candidate.is_absolute():
            return candidate
        workspace_root = str(state.workspace_root or "").strip()
        if workspace_root:
            return Path(workspace_root) / candidate
        return candidate

    @staticmethod
    def _candidate_file_exists(state: CyberGymState, path: str) -> bool:
        if not str(path or "").strip():
            return False
        try:
            return ValidationMixin._resolve_candidate_path(state, path).is_file()
        except (OSError, ValueError):
            return False
