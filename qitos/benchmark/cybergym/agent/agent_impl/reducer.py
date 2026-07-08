"""Reduce orchestration helpers — extracted from agent.py reduce()."""
from __future__ import annotations

import json
from typing import Any

from qitos.core.decision import Decision
from qitos.core.tool_result import ToolResult

from ..agent_impl.prompt.phase import phase_local_steps
from ..agent_impl.core.runtime_context_contract import bump_context_revision
from ..family_runtime import CandidateRecord
from ..state import CyberGymState


# ------------------------------------------------------------------
# Exploration phase auto-detect completion
# ------------------------------------------------------------------

def apply_exploration_completion(agent: Any, state: CyberGymState) -> None:
    """Auto-detect exploration completion and set advisory hints.

    When the agent has built sufficient understanding for at least one
    sink candidate, mark exploration as complete so the phase engine can
    transition.  Also adds callee-check advisory hints for unexplored
    downstream functions.
    """
    if getattr(state, "current_phase", "") != "exploration":
        return
    nodes = list(getattr(state, "call_chain_nodes", []) or [])
    gates = list(getattr(state, "call_chain_gates", []) or [])
    active_sinks = state.confirmed_sink_candidates()
    if not active_sinks:
        # Without a sink candidate, do NOT set exploration_complete
        # even if nodes/gates exist — the agent must propose a sink first.
        return
    state.exploration_complete = True
    primary = state._primary_sink_id()
    for sink in active_sinks:
        sid = f"{sink.function}@{sink.location}"
        sink_nodes = [n for n in nodes
                      if n.sink_id == sid or (not n.sink_id and sid == primary)]
        sink_confirmed = any(
            g.status == "confirmed" and (g.sink_id == sid or (not g.sink_id and sid == primary))
            for g in gates
        )
        if len(sink_nodes) >= 2 and sink_confirmed:
            break
    # Callee-check: soft advisory hint
    if state.exploration_complete and active_sinks:
        svc = agent._analysis_service(state)
        if svc is not None and svc.index_status in {"GRAPH_READY", "PARTIAL_INDEX"}:
            primary_sink = active_sinks[0]
            explored_funcs = {n.function for n in nodes if n.function}
            unexplored = []
            for sym in svc.symbols:
                if sym.name == primary_sink.function or sym.qualified_name == primary_sink.function:
                    for edge in svc.edges:
                        if edge.caller_id == sym.symbol_id:
                            callee = next((s for s in svc.symbols if s.symbol_id == edge.callee_id), None)
                            if callee and callee.name not in explored_funcs:
                                unexplored.append(callee.name)
                    break
            if unexplored[:3]:
                hints = list(state.metadata.get("_callee_gate_hints", []) or [])
                hints.append(
                    f"[ADVISORY] Your sink candidate {primary_sink.function} calls "
                    f"{', '.join(unexplored[:3])} which haven't been explored. "
                    "You can proceed, but tracing these callees may improve your PoC."
                )
                state.metadata["_callee_gate_hints"] = hints


# ------------------------------------------------------------------
# Phase advancement
# ------------------------------------------------------------------

def advance_phase(agent: Any, state: CyberGymState, step: int) -> tuple[str, str]:
    """Advance the phase engine and return (old_phase, new_phase).

    Handles PhaseEngine advancement, manual phase switch, and phase-local
    step counting.  Also calls agent._update_control_mode().
    """
    try:
        state.current_step = int(step)
    except Exception:
        pass
    state.phase_local_steps = phase_local_steps(state)
    old_phase = state.current_phase
    manual_phase = str(state.metadata.pop("_manual_phase_switch", "") or "")
    if manual_phase:
        new_phase = manual_phase
    else:
        new_phase = agent._phase_engine.advance(state, step)
    state.current_phase = new_phase
    # Cache phase for TUI rendering (on_before_step fires before next prepare())
    state.metadata["_tui_phase"] = new_phase
    if new_phase != old_phase:
        state.phase_enter_step = int(step)
        state.phase_local_steps = 0
        state.phase_submissions = 0
        if old_phase == "verification" and new_phase == "investigation":
            state.reinvestigate_requested = False
        state.phase_read_actions = 0
        state.repeated_read_target = ""
        state.repeated_read_count = 0
    else:
        state.phase_local_steps = phase_local_steps(state)

    # Formulation step-budget: force candidate_required after 6 steps
    # with no PoC, preventing the agent from reading code indefinitely.
    if (
        new_phase == "formulation"
        and state.phase_local_steps >= 6
        and not any(
            bool(str(getattr(item, "file_path", "") or "").strip())
            for item in list(getattr(state, "ready_pocs", []) or [])
        )
    ):
        state.candidate_required = True

    agent._update_control_mode(state, int(step))
    return old_phase, new_phase


# ------------------------------------------------------------------
# Exploration phase checkpoints
# ------------------------------------------------------------------

def apply_exploration_checkpoints(state: CyberGymState) -> None:
    """Apply exploration-phase checkpoint logic.

    Sets pending sink/chain/gates checkpoints based on phase-local step count
    and task-spec confidence.
    """
    if state.current_phase != "exploration":
        return
    nodes = list(getattr(state, "call_chain_nodes", []) or [])
    gates = list(getattr(state, "call_chain_gates", []) or [])
    active_sinks = state.confirmed_sink_candidates()
    pl_steps = phase_local_steps(state)
    # Adaptive sink candidate checkpoint: rich descriptions -> nudge earlier
    if not active_sinks and not getattr(state, "pending_sink_checkpoint", False):
        conf = float(getattr(state, "task_spec_confidence", 0.5) or 0.5)
        if conf >= 0.6 and pl_steps >= 1:
            state.pending_sink_checkpoint = True
        elif conf >= 0.4 and pl_steps >= 2:
            state.pending_sink_checkpoint = True
        elif pl_steps >= 3:
            state.pending_sink_checkpoint = True
    if not nodes and pl_steps >= 2 and not state.pending_chain_checkpoint:
        state.pending_chain_checkpoint = True
    if nodes and not any(g.status == "confirmed" for g in gates) and pl_steps >= 4:
        if not state.pending_gates_checkpoint:
            state.pending_gates_checkpoint = True


# ------------------------------------------------------------------
# Investigation phase checkpoints
# ------------------------------------------------------------------

def apply_investigation_checkpoints(state: CyberGymState) -> None:
    """Apply investigation-phase checkpoint logic.

    Handles constraint checkpoint, gates checkpoint, and empty constraint
    board soft reminder.
    """
    if state.current_phase != "investigation":
        return
    # Constraint checkpoint: force chain node recording when board is empty
    if (not state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_chain_checkpoint):
        pl_steps = phase_local_steps(state)
        if pl_steps > 0 and pl_steps % 5 == 0:
            state.pending_chain_checkpoint = True

    # Gates checkpoint: nudge for gates when nodes exist but no gates
    if (state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_gates_checkpoint
            and not state.pending_chain_checkpoint):
        pl_steps = phase_local_steps(state)
        if pl_steps > 0 and pl_steps % 7 == 0:
            state.pending_gates_checkpoint = True

    # Empty constraint board soft reminder
    if (not state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_chain_checkpoint
            and phase_local_steps(state) >= 4
            and not state.pending_reminder):
        state.pending_reminder = (
            "No chain nodes recorded yet. Use find_symbols to find the "
            "vulnerable function, then record_chain_node to add it to the "
            "chain, or record_gate to add a path constraint."
        )
        state.pending_reminder_signature = "empty-constraint-board"


# ------------------------------------------------------------------
# Sink rotation on repeated failure
# ------------------------------------------------------------------

def apply_sink_rotation(agent: Any, state: CyberGymState) -> None:
    """Handle sink rotation on repeated failure.

    When consecutive misses reach 2, try rotating to the next sink candidate.
    V12: lowered from 3 to 2 for faster hypothesis correction.
    """
    if (state.consecutive_misses >= 2
            and not state.reinvestigate_requested
            and agent._advance_sink_candidate(state)):
        state.pending_reminder = (
            "Rotated to next sink candidate after repeated failures. "
            "The previous sink's constraints may not be reachable — "
            "try the new sink's approach."
        )
        state.pending_reminder_signature = "sink-rotation"


# ------------------------------------------------------------------
# Consecutive-miss reinvestigation nudge
# ------------------------------------------------------------------

def apply_consecutive_miss_nudge(state: CyberGymState) -> None:
    """Add reinvestigation nudge after 4+ consecutive misses."""
    if (state.consecutive_misses >= 4
            and not state.pending_reminder):
        state.pending_reminder = (
            f"{state.consecutive_misses} consecutive no-crash submissions. "
            "STOP submitting variants until you classify the miss: either the input is "
            "not reaching the vulnerable path, or it reaches the path but fails the "
            "sink trigger condition. READ the harness/path and inspect PoC bytes to "
            "choose one concrete fix before the next submit."
        )
        state.pending_reminder_signature = "consecutive-miss-reinvestigate"


# ------------------------------------------------------------------
# Candidate building from recipe
# ------------------------------------------------------------------

def try_build_candidate_from_recipe(state: CyberGymState) -> None:
    """Try building a candidate from recipe if no candidates exist.

    Fix D: When the ready_pocs and candidate_queue are both empty,
    attempt to build a candidate from the active recipe.
    """
    try:
        from ..agent_impl.poc.candidate_builder import build_candidate_from_recipe
        build_result = build_candidate_from_recipe(state)
        state.metadata["last_poc_build_result"] = build_result
        if build_result.get("status") == "success" and build_result.get("candidate_path"):
            fingerprint = str(build_result.get("content_fingerprint") or "")
            existing_fingerprints = {
                str(getattr(item, "content_fingerprint", "") or "")
                for item in state.ready_pocs
                if str(getattr(item, "content_fingerprint", "") or "")
            }
            if not fingerprint or fingerprint not in existing_fingerprints:
                state.ready_pocs.append(
                    CandidateRecord(
                        candidate_id=str(build_result.get("candidate_id") or f"recipe_{build_result.get('recipe_id', 'x')}"),
                        family_id=str(build_result.get("family_id") or f"recipe:{build_result.get('recipe_id', 'x')}"),
                        file_path=str(build_result["candidate_path"]),
                        content_fingerprint=fingerprint,
                        mutation_summary="built from recipe",
                        expected_signal="submit_for_feedback",
                        novelty_note="recipe-driven build",
                        base_seed="",
                        generation_method=str(build_result.get("generation_method") or "recipe"),
                        ready_to_submit=True,
                        priority=5,
                        producer_agent="main_agent",
                        fingerprint_mode="artifact",
                        artifact_sha256=fingerprint,
                    )
                )
            bump_context_revision(state, "poc_recipe")
            bump_context_revision(state, "candidate")
    except Exception:
        pass


# ------------------------------------------------------------------
# Exchange logger
# ------------------------------------------------------------------

def log_exchange(
    agent: Any,
    state: CyberGymState,
    decision: Any,
    action_results: list,
) -> None:
    """Log exchange for debugging (messages, response, observations)."""
    if agent._exchange_logger is None:
        return
    step_id = getattr(state, "current_step", 0) or 0
    # Log model response
    if isinstance(decision, Decision):
        resp: dict[str, Any] = {}
        if decision.tool_calls:
            resp["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": str(tc.args or "")[:500]}}
                for tc in decision.tool_calls
            ]
        if decision.text:
            resp["text"] = decision.text[:1000]
        agent._exchange_logger.log_response(step_id, resp)
    # Log observations (tool results)
    obs_texts: list[str] = []
    for tr in action_results:
        if isinstance(tr, ToolResult):
            obs_texts.append(tr.text[:4000])
        elif isinstance(tr, dict):
            obs_texts.append(json.dumps(tr, ensure_ascii=False, default=str)[:4000])
    agent._exchange_logger.log_observations(step_id, obs_texts)
    agent._exchange_logger.flush()
