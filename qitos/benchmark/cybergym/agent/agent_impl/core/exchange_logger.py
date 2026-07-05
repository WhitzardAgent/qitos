"""ExchangeLogger -- persist the raw LLM I/O exchange for debugging.

Writes a JSONL file (``.cybergym/exchange_trace.jsonl``) where each line is
one step's exchange:

    {
      "step_id": N,
      "messages_sent": [...],      // the actual messages array sent to the model
      "model_response": {...},     // raw API response (tool_calls, text, usage)
      "observations": [...]        // rendered tool-result strings that entered context
    }

Enabled when ``CYBERGYM_EXCHANGE_LOG=1`` (default: off).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


_EXCHANGE_LOG_ENABLED: bool = os.environ.get(
    "CYBERGYM_EXCHANGE_LOG", "0"
).strip() in {"1", "true", "yes"}


class ExchangeLogger:
    """Lightweight exchange logger that hooks into the engine step loop."""

    def __init__(self, workspace_root: str) -> None:
        self._path = Path(workspace_root) / ".cybergym" / "exchange_trace.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._current_step: Dict[str, Any] = {}

    def log_messages(self, step_id: int, messages: List[Dict[str, Any]]) -> None:
        """Store the messages array sent to the model."""
        self._current_step.setdefault("step_id", step_id)
        self._current_step["step_id"] = step_id
        # Trim large content fields to keep the log manageable
        self._current_step["messages_sent"] = _trim_messages(messages)

    def log_response(self, step_id: int, response: Dict[str, Any]) -> None:
        """Store the raw model response."""
        self._current_step.setdefault("step_id", step_id)
        self._current_step["model_response"] = _trim_response(response)

    def log_observations(self, step_id: int, observations: List[str]) -> None:
        """Store the observation strings added to context."""
        self._current_step.setdefault("step_id", step_id)
        self._current_step["observations"] = [
            o[:4000] if len(o) > 4000 else o for o in observations
        ]

    def flush(self) -> None:
        """Write the current step to the JSONL file and reset."""
        if not self._current_step:
            return
        try:
            line = json.dumps(self._current_step, ensure_ascii=False, default=str)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self._current_step = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_CONTENT_PREVIEW = 2000


def _trim_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trim message content to keep the log file manageable."""
    trimmed = []
    for msg in messages:
        m = dict(msg)
        role = m.get("role", "")
        content = m.get("content")
        if isinstance(content, str) and len(content) > _MAX_CONTENT_PREVIEW:
            m["content"] = content[:_MAX_CONTENT_PREVIEW] + f"... [{len(content)} chars total]"
        elif isinstance(content, list):
            # Multi-part content (e.g., text + image)
            m["content"] = [
                {**part, "text": part["text"][:_MAX_CONTENT_PREVIEW] + "..."}
                if isinstance(part, dict) and isinstance(part.get("text"), str)
                   and len(part["text"]) > _MAX_CONTENT_PREVIEW
                else part
                for part in content
            ]
        # Tool calls in assistant messages
        if "tool_calls" in m and isinstance(m["tool_calls"], list):
            m["tool_calls"] = [
                {**tc, "function": {**tc.get("function", {}),
                 "arguments": tc.get("function", {}).get("arguments", "")[:500]}}
                if isinstance(tc, dict) else tc
                for tc in m["tool_calls"]
            ]
        trimmed.append(m)
    return trimmed


def _trim_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Trim model response for logging."""
    r = dict(response)
    # Keep tool_calls structure but trim arguments
    if "tool_calls" in r and isinstance(r["tool_calls"], list):
        r["tool_calls"] = [
            {**tc, "function": {**tc.get("function", {}),
             "arguments": tc.get("function", {}).get("arguments", "")[:500]}}
            if isinstance(tc, dict) else tc
            for tc in r["tool_calls"]
        ]
    return r


def get_exchange_logger(workspace_root: str) -> Optional[ExchangeLogger]:
    """Return an ExchangeLogger if enabled, else None."""
    if not _EXCHANGE_LOG_ENABLED:
        return None
    return ExchangeLogger(workspace_root)
