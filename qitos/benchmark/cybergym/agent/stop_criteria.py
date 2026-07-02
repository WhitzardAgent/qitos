"""Custom stop criteria for CyberGym PoC verification."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from qitos.core.errors import StopReason
from qitos.engine.stop_criteria import StopCriteria

# Official CyberGym protocol: the agent only gets VUL-SIDE feedback (public
# /submit-vul) and decides its own stopping; the fix-side discriminant is the
# evaluator's private post-hoc job (verify_agent_result.py). When this is on,
# the agent does NOT stop on vul-crash alone — it keeps refining for precision
# until true acceptance (is_verified) or max_steps. No fix-side information
# leaks into the agent's run. Scoring still uses the fix discriminant
# separately. Set CYBERGYM_VUL_ONLY_FEEDBACK=0 to revert.
VUL_ONLY_FEEDBACK = os.environ.get(
    "CYBERGYM_VUL_ONLY_FEEDBACK", "1"
).strip().lower() not in {"0", "false", "no", "off"}


class PoCVerificationCriteria(StopCriteria):
    """Stop when the PoC has been verified as successful by the CyberGym server.

    A PoC is considered successful when:
    - Full verification accepts the candidate (is_verified() is True).

    When VUL_ONLY_FEEDBACK is on, a vul-only trigger (crash without fix-side
    data) is treated as PARTIAL success — the agent keeps refining for precision
    rather than stopping. Only true acceptance (accepted=True or both vul/fix
    exit codes available and passing) triggers a stop.
    """

    def __init__(self, max_attempts: int | None = None):
        # Preserve the old signature for compatibility, but do not stop on attempts.
        self.max_attempts = max_attempts

    def should_stop(
        self,
        state: Any,
        step_count: int,
        runtime_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[StopReason], Optional[str]]:
        if VUL_ONLY_FEEDBACK:
            # Official CyberGym protocol: the agent only sees the vul-side
            # result and stops on the FIRST vul-side crash, saving that PoC.
            # The fix-side discriminant is the evaluator's private post-hoc job
            # and never reaches the agent. (is_verified() is kept as a fast
            # path for the legacy case where fix data somehow exists.)
            if hasattr(state, "is_verified") and state.is_verified():
                return (
                    True,
                    StopReason.SUCCESS,
                    "PoC verified by full task verification",
                )
            if hasattr(state, "vul_crashed") and state.vul_crashed():
                return (
                    True,
                    StopReason.SUCCESS,
                    "PoC crashed the vulnerable target (vul-side stop)",
                )
            return False, None, None

        # Legacy (leaky) behavior: stop on the full fix-side verdict.
        if not hasattr(state, "is_verified"):
            return False, None, None
        if state.is_verified():
            return (
                True,
                StopReason.SUCCESS,
                "PoC verified by full task verification",
            )
        return False, None, None


class PhaseExitCriteria(StopCriteria):
    """Stop when agent has exited the specified phase.

    Used for evaluation pipelines that only need to run through a
    specific phase (e.g., exploration) without completing the full
    PoC generation cycle.
    """

    def __init__(self, phase: str = "exploration"):
        self.target_phase = phase

    def should_stop(
        self,
        state: Any,
        step_count: int,
        runtime_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[StopReason], Optional[str]]:
        if step_count <= 0:
            return False, None, None
        current = getattr(state, "current_phase", "")
        if current != self.target_phase:
            return (
                True,
                StopReason.AGENT_CONDITION,
                f"Exited {self.target_phase} phase (now: {current})",
            )
        return False, None, None

