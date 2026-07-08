"""CyberGym phase engine definition."""

from __future__ import annotations

from qitos.kit.planning.phase_engine import PhaseEngine, PhaseSpec, TransitionRule

from ...state import CyberGymState


def phase_local_steps(state: CyberGymState) -> int:
    """Return steps spent in the current phase, independent of global task step."""
    try:
        current = int(getattr(state, "current_step", 0) or 0)
        entered = int(getattr(state, "phase_enter_step", 0) or 0)
    except Exception:
        return int(getattr(state, "phase_local_steps", 0) or 0)
    return max(0, current - entered)


def _ingestion_ready(s: CyberGymState) -> bool:
    """Ingestion is complete when the LLM has done meaningful analysis.

    Requirements:
    - crash_type is set OR 4+ phase-local steps elapsed
    - At least one model-confirmed sink candidate (not just description-derived)
    - OR 4+ phase-local steps have elapsed (hard fallback)

    Pre-populated description/harness_chain candidates do NOT count as evidence
    of LLM analysis — they are created deterministically during init_state().
    """
    phase_steps = phase_local_steps(s)

    # Hard fallback: allow transition after 4 steps regardless
    if phase_steps >= 4:
        return True

    # crash_type must be set (not UNSET, not empty)
    crash_prior = bool((s.metadata or {}).get("crash_type_prior"))
    crash_set = crash_prior or bool(str(getattr(s, "crash_type", "") or "").strip())

    # At least one NON-description-derived sink candidate
    # (description/harness_chain sources are pre-populated by init_state,
    #  not evidence the LLM actually analyzed the code)
    provisional_sources = {"description", "harness_chain"}
    model_sinks = [
        c for c in list(getattr(s, "sink_candidates", []) or [])
        if c.source not in provisional_sources
        and c.status != "eliminated"
        and c.status != "provisional"
        and not bool((c.metadata or {}).get("requires_review"))
    ]
    sink_confirmed = len(model_sinks) > 0

    # Require crash_type + at least one model-confirmed sink
    return crash_set and sink_confirmed


def _exploration_step_limit(s: CyberGymState) -> int:
    """Dynamic step limit based on description informativeness.

    Reduced from 8/10/12 to 6/8/10 — sink hypothesis allows earlier
    PoC attempts, and dynamic feedback replaces prolonged static analysis.
    """
    conf = float(getattr(s, "task_spec_confidence", 0.5) or 0.5)
    if conf >= 0.6:
        return 6  # rich description, quick transition
    elif conf < 0.4:
        return 10  # vague description, still faster
    return 8


def cybergym_phase_engine() -> PhaseEngine:
    """Build the four-phase state machine for CyberGym PoC generation."""
    return PhaseEngine(
        phases=[
            PhaseSpec(
                name="ingestion",
                max_steps=5,
                transitions=[
                    # P40: require structured analysis, not just description existence
                    TransitionRule(
                        target="exploration",
                        condition=lambda s: _ingestion_ready(s),
                        priority=10,
                    ),
                ],
            ),
            PhaseSpec(
                name="exploration",
                max_steps=None,
                transitions=[
                    # Agent-driven: agent sets exploration_complete when it has
                    # enough understanding to start PoC construction.
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: getattr(s, "exploration_complete", False),
                        priority=10,
                    ),
                    # Safety fallback: dynamic step limit based on description
                    # detail.  Vague descriptions get more exploration budget.
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: phase_local_steps(s) >= _exploration_step_limit(s),
                        priority=0,
                    ),
                ],
            ),
            PhaseSpec(
                name="investigation",
                max_steps=None,
                transitions=[
                    # V12: any confirmed sink allows early transition to formulation.
                    # Sink is a hypothesis — dynamic feedback from PoC attempts is
                    # more valuable than completing full constraint analysis.
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: bool(s.confirmed_sink_candidates()),
                        priority=15,
                    ),
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
                    # Fallback: force transition after 8 steps.
                    # V12: lowered from 15 — early PoC attempts with dynamic
                    # feedback are more valuable than prolonged static analysis.
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: phase_local_steps(s) >= 8,
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
                    # Reinvestigation: after repeated failures, go back to
                    # investigation to re-read code and update hypotheses.
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: (
                            s.reinvestigate_requested
                            and int(getattr(s, 'consecutive_misses', 0) or 0) >= 2
                        ),
                        priority=15,
                    ),
                    # Fallback: after 6 phase-local steps with no PoC, force
                    # candidate_required to push the agent toward building one.
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: (
                            phase_local_steps(s) >= 6
                            and not any(
                                bool(str(getattr(item, "file_path", "") or "").strip())
                                for item in list(getattr(s, "ready_pocs", []) or [])
                            )
                        ),
                        priority=0,
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
