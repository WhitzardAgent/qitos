"""CyberGym phase engine definition."""

from __future__ import annotations

from qitos.kit.planning.phase_engine import PhaseEngine, PhaseSpec, TransitionRule

from ..state import CyberGymState


def phase_local_steps(state: CyberGymState) -> int:
    """Return steps spent in the current phase, independent of global task step."""
    try:
        current = int(getattr(state, "current_step", 0) or 0)
        entered = int(getattr(state, "phase_enter_step", 0) or 0)
    except Exception:
        return int(getattr(state, "phase_local_steps", 0) or 0)
    return max(0, current - entered)


def _ingestion_ready(s: CyberGymState) -> bool:
    """P40: ingestion should not be a no-op. Require at least:
    - bug_type has been classified (even if empty string = 'attempted')
    - OR at least one code artifact identified from the description
    - OR 2+ phase-local steps have elapsed (fallback)
    """
    # bug_type is set by _classify_bug_type even when no pattern matches
    # (it returns ""), but the metadata flag tells us classification was
    # attempted.  If bug_type is non-empty, that's a strong signal.
    if getattr(s, "bug_type", "") or getattr(s, "metadata", {}).get("_bug_type_classified"):
        return True
    # Source files or symbols extracted from the description
    if getattr(s, "source_files_mentioned", None) or getattr(s, "symbols_mentioned", None):
        return True
    return False


def cybergym_phase_engine() -> PhaseEngine:
    """Build the four-phase state machine for CyberGym PoC generation."""
    return PhaseEngine(
        phases=[
            PhaseSpec(
                name="ingestion",
                max_steps=3,
                transitions=[
                    # P40: require structured analysis, not just description existence
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: _ingestion_ready(s),
                        priority=10,
                    ),
                ],
            ),
            PhaseSpec(
                name="investigation",
                max_steps=None,
                transitions=[
                    # P41: primary transition requires EITHER confirmed chain
                    # gates OR legacy constraint progress OR trigger_hypothesis.
                    # The chain-gate check ensures the agent has understood the
                    # path, not just found function names from the description.
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: bool(
                            s.trigger_hypothesis
                            or s.vulnerable_functions
                            or s.vulnerable_files
                        ) and (
                            # New: at least one confirmed chain gate
                            any(
                                g.status == "confirmed"
                                for g in list(getattr(s, "call_chain_gates", []) or [])
                            )
                            # Legacy: at least one confirmed/hypothesized
                            # PathConstraint from source reading
                            or any(
                                str(getattr(c, "status", "") or "").strip()
                                in {"confirmed", "hypothesized"}
                                for c in list(getattr(s, "path_constraints", []) or [])
                            )
                            # If trigger_hypothesis is set, the agent has
                            # articulated a plan — allow transition.
                            or bool(s.trigger_hypothesis)
                        ),
                        priority=10,
                    ),
                    # Fallback: force transition after 15 steps (was 10, raised
                    # to give more time for constraint discovery and checkpoint).
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: phase_local_steps(s) >= 15,
                        priority=0,
                    ),
                ],
            ),
            PhaseSpec(
                name="formulation",
                max_steps=None,
                transitions=[
                    TransitionRule(
                        target="verification",
                        condition=lambda s: any(
                            bool(str(getattr(item, "file_path", "") or "").strip())
                            and bool(getattr(item, "ready_to_submit", True))
                            for item in list(getattr(s, "ready_pocs", []) or [])
                        ),
                        priority=10,
                    ),
                ],
            ),
            PhaseSpec(
                name="verification",
                transitions=[
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: (
                            s.reinvestigate_requested
                            and s.last_verification_result
                        ),
                        priority=20,
                    ),
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: (
                            s.last_verification_result
                            and not s.is_verified()
                        ),
                        priority=10,
                    ),
                ],
            ),
        ],
        initial_phase="ingestion",
        state_attr="current_phase",
    )
