"""Shared utilities for agent reduce() methods."""

from __future__ import annotations

from typing import Any, Dict, Optional


def extract_tool_results(observation: Any) -> Dict[str, Any]:
    """Extract tool name -> output mapping from observation.

    The engine wraps tool execution results as ToolResult objects
    (with .output attribute) or dicts. This helper normalizes them
    into a dict keyed by tool name or output type.
    """
    results: Dict[str, Any] = {}
    action_results = []
    if hasattr(observation, "action_results"):
        action_results = list(observation.action_results or [])
    elif isinstance(observation, dict):
        action_results = list(observation.get("action_results", []))
    for ar in action_results:
        output = getattr(ar, "output", None)
        if output is None and isinstance(ar, dict):
            output = ar.get("output", ar)
        if isinstance(output, dict):
            # Key by tool name (from "name" or "type" field) or by index
            name = output.get("name", output.get("type", ""))
            if name:
                results[name] = output
            else:
                results[f"tool_{len(results)}"] = output
    return results


def inject_execution_context(agent: Any, prompt: str) -> str:
    """Append execution context XML to a system prompt if available on the agent.

    Used by specialist agents (pentester, coder, installer, searcher, etc.)
    to receive awareness of the global task and subtask progress.
    """
    ctx = getattr(agent, '_execution_context', '')
    if ctx:
        prompt += f"\n\n# Execution Context\n{ctx}"
    return prompt
