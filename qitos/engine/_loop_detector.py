"""Tool-call loop detection for runtime recovery.

Supports escalating responses:
- Soft warning at warn_repeats (default 3): injected into observation as guidance
- Hard block at max_repeats (default 7): returns error, prevents execution
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def _args_hash(args: Dict[str, Any], strip_keys: Optional[List[str]] = None) -> str:
    filtered = args
    if strip_keys:
        filtered = {k: v for k, v in args.items() if k not in strip_keys}
    try:
        return json.dumps(filtered, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(filtered)


@dataclass
class LoopCheckResult:
    """Result of a loop detection check."""

    level: str  # "ok", "warn", "block"
    repeats: int = 0
    message: Optional[str] = None


@dataclass
class ToolCallLoopDetector:
    """Detect repeated tool calls with identical arguments.

    Escalation levels:
    - warn_repeats (default 3): inject soft warning into the observation
    - max_repeats (default 7): block the tool call entirely

    When strip_volatile is True, volatile argument keys (like "message",
    "reason") are stripped before comparison. This detects semantically
    identical repeats where only the message text differs.
    """

    warn_repeats: int = 3
    max_repeats: int = 7
    strip_volatile: bool = False
    volatile_arg_keys: List[str] = field(default_factory=lambda: [
        "message", "summary", "reason", "rationale",
    ])
    _history: List[Tuple[str, str]] = field(default_factory=list)

    def check(self, tool_name: str, args: Dict[str, Any]) -> str | None:
        """Backward-compatible check — returns message only for hard blocks."""
        result = self.check_detailed(tool_name, args)
        if result.level == "block":
            return result.message
        return None

    def check_detailed(self, tool_name: str, args: Dict[str, Any]) -> LoopCheckResult:
        """Check with detailed result including warning level."""
        strip_keys = self.volatile_arg_keys if self.strip_volatile else None
        key = (str(tool_name or ""), _args_hash(dict(args or {}), strip_keys=strip_keys))
        if not key[0]:
            return LoopCheckResult(level="ok")

        repeats = 0
        for item in reversed(self._history):
            if item == key:
                repeats += 1
            else:
                break

        if repeats >= self.max_repeats:
            return LoopCheckResult(
                level="block",
                repeats=repeats + 1,
                message=(
                    f"You have called `{key[0]}` with the same arguments {repeats + 1} times. "
                    "This tool call is blocked. Use a different approach or tool."
                ),
            )

        if repeats >= self.warn_repeats:
            return LoopCheckResult(
                level="warn",
                repeats=repeats + 1,
                message=(
                    f"WARNING: You have called `{key[0]}` with the same arguments {repeats + 1} times. "
                    "Consider using a different tool or changing your arguments based on the results you've seen."
                ),
            )

        return LoopCheckResult(level="ok", repeats=repeats + 1)

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        strip_keys = self.volatile_arg_keys if self.strip_volatile else None
        key = (str(tool_name or ""), _args_hash(dict(args or {}), strip_keys=strip_keys))
        self._history.append(key)
        if len(self._history) > 128:
            self._history = self._history[-128:]

    def reset(self) -> None:
        self._history = []


__all__ = ["ToolCallLoopDetector", "LoopCheckResult"]
