"""Multi-agent evaluation utilities: per-agent cost breakdown and metrics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


def per_agent_breakdown(
    steps: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Compute per-agent token/cost breakdown from step records.

    Args:
        steps: List of step dictionaries (from load_run_artifacts).

    Returns:
        Dict mapping agent_id -> {step_count, tokens_in, tokens_out, tokens_total, cost}.
    """
    by_agent: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"step_count": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0, "cost": 0.0}
    )

    for step in steps:
        agent_id = step.get("agent_id") or "default"
        entry = by_agent[agent_id]
        entry["step_count"] += 1

        # Extract token usage from tool_invocations or step metadata
        meta = step.get("metadata", {})
        if isinstance(meta, dict):
            tokens_in = meta.get("tokens_in", 0) or 0
            tokens_out = meta.get("tokens_out", 0) or 0
            entry["tokens_in"] += tokens_in
            entry["tokens_out"] += tokens_out
            entry["tokens_total"] += tokens_in + tokens_out

        # Cost from step
        cost = step.get("cost", 0.0) or 0.0
        entry["cost"] += cost

    return dict(by_agent)


def handoff_metrics(
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute handoff-related metrics from step records.

    Returns:
        Dict with handoff_count, unique_agents, agent_sequence, handoff_overhead_steps.
    """
    handoff_count = 0
    agent_sequence: List[str] = []
    prev_agent = None
    overhead_steps = 0

    for step in steps:
        agent_id = step.get("agent_id") or "default"
        if agent_id != prev_agent:
            if prev_agent is not None:
                handoff_count += 1
                # The first step of a new agent is "overhead" (context building)
                overhead_steps += 1
            agent_sequence.append(agent_id)
            prev_agent = agent_id

    return {
        "handoff_count": handoff_count,
        "unique_agents": len(set(agent_sequence)),
        "agent_sequence": agent_sequence,
        "handoff_overhead_steps": overhead_steps,
    }


def delegation_metrics(
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute delegation-related metrics from trace events.

    Returns:
        Dict with delegate_count, fanout_count, delegate_success_rate,
        fanout_avg_tasks, fanout_success_rate.
    """
    delegate_count = 0
    delegate_success = 0
    fanout_count = 0
    fanout_tasks = []
    fanout_success = 0

    for event in events:
        phase = event.get("phase", "")
        payload = event.get("payload", {})

        if phase == "DELEGATE_END":
            delegate_count += 1
            if payload.get("status") == "success":
                delegate_success += 1

        if phase == "FANOUT_START":
            fanout_count += 1
            fanout_tasks.append(payload.get("task_count", 0))

        if phase == "FANOUT_END":
            total = payload.get("total", 0)
            succeeded = payload.get("succeeded", 0)
            if total > 0 and succeeded == total:
                fanout_success += 1

    return {
        "delegate_count": delegate_count,
        "delegate_success_rate": delegate_success / max(delegate_count, 1),
        "fanout_count": fanout_count,
        "fanout_avg_tasks": sum(fanout_tasks) / max(len(fanout_tasks), 1),
        "fanout_success_rate": fanout_success / max(fanout_count, 1),
    }
