"""Typed state for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.state import StateSchema


@dataclass
class CyberGymState(StateSchema):
    """State for the CyberGym PoC Generation Agent.

    Tracks vulnerability context, investigation findings, planning,
    PoC iteration progress, and phase tracking across the four-phase
    state machine (Ingestion -> Investigation -> Formulation -> Verification).
    """

    # Override defaults for PoC generation tasks
    max_steps: int = 30

    # Vulnerability context (stable across steps)
    vulnerability_description: str = ""
    cve_id: str = ""
    bug_type: str = ""  # buffer_overflow, use_after_free, integer_overflow, etc.
    affected_component: str = ""

    # CyberGym task metadata
    task_id: str = ""
    agent_id: str = ""
    checksum: str = ""
    server_url: str = ""

    # Investigation findings
    vulnerable_files: List[str] = field(default_factory=list)
    vulnerable_functions: List[str] = field(default_factory=list)
    input_entry_points: List[str] = field(default_factory=list)
    trigger_hypothesis: str = ""
    repo_index: str = ""

    # Harness info (populated during ingestion from submit.sh)
    harness_info: str = ""  # binary path and arguments from submit.sh
    corpus_files: List[str] = field(default_factory=list)  # discovered fuzzing corpus/sample files
    poc_strategy: str = ""  # auto-detected: text, binary_python, corpus_mutate, hex

    # Planning
    plan: List[str] = field(default_factory=list)
    plan_cursor: int = 0

    # PoC iteration
    poc_path: str = ""
    poc_attempts: int = 0
    last_error_trace: str = ""
    last_verification_result: Dict[str, Any] = field(default_factory=dict)

    # PoC quality tracking (regression protection)
    best_poc_path: str = ""
    best_poc_score: int = 0  # 0=miss, 1=partial(vul crashes), 2=success(discriminant)
    discriminant_failed: bool = False  # True when fix_exit != 0 (PoC too aggressive)
    crash_type: str = ""  # parsed from sanitizer output (e.g., heap-buffer-overflow)
    crash_location: str = ""  # parsed from sanitizer output (file:line)

    # Phase tracking
    current_phase: str = "ingestion"  # ingestion | investigation | formulation | verification

    # Workspace paths
    workspace_root: str = ""
    repo_dir: str = ""  # path to extracted repo inside workspace

    def is_verified(self) -> bool:
        """Check if the PoC has been verified as successful by the server."""
        result = self.last_verification_result
        if not result:
            return False
        vul_code = result.get("vul_exit_code")
        fix_code = result.get("fix_exit_code")
        # Success: vulnerable binary crashes (non-zero) and patched binary doesn't (zero)
        # or they differ in behavior
        if vul_code is None:
            return False
        if vul_code != 0 and (fix_code is None or fix_code == 0):
            return True
        if vul_code != 0 and fix_code is not None and vul_code != fix_code:
            return True
        return False

    def _update_best_poc(self, score: int) -> None:
        """Update best PoC tracking after verification.

        Score: 0=miss (vul doesn't crash), 1=partial (both crash), 2=success (discriminant)
        """
        if score > self.best_poc_score and self.poc_path:
            self.best_poc_score = score
            self.best_poc_path = self.poc_path
