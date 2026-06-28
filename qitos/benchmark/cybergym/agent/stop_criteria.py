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
            # Only stop on TRUE acceptance (full verification with fix
            # discriminant).  When verification_scope is "vul_only", we have
            # no fix-side data so the agent must keep refining for precision.
            if hasattr(state, "is_verified") and state.is_verified():
                return (
                    True,
                    StopReason.SUCCESS,
                    "PoC verified by full task verification",
                )
            # Vul crashed but no fix-side data — partial success, keep
            # refining. The agent should treat this as a signal to improve
            # precision rather than stop.
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

