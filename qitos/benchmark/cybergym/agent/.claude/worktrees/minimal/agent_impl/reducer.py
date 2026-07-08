"""Minimal reducer helpers for the Minimal CyberGym Agent.

No phase advancement, no checkpoints, no sink rotation, no
candidate building from recipe. Only the exchange logger remains.
"""
from __future__ import annotations

import json
from typing import Any

from qitos.core.decision import Decision
from qitos.core.tool_result import ToolResult

from ..state import CyberGymState


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
