"""Tool-call loop detection for runtime recovery."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


def _args_hash(args: Dict[str, Any]) -> str:
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(args)


@dataclass
class ToolCallLoopDetector:
    """Detect repeated tool calls with identical arguments."""

    max_repeats: int = 3
    _history: List[Tuple[str, str]] = field(default_factory=list)

    def check(self, tool_name: str, args: Dict[str, Any]) -> str | None:
        key = (str(tool_name or ""), _args_hash(dict(args or {})))
        if not key[0]:
            return None
        repeats = 0
        for item in reversed(self._history):
            if item == key:
                repeats += 1
            else:
                break
        if repeats >= max(1, int(self.max_repeats)):
            return (
                f"You have called `{key[0]}` with the same arguments {repeats + 1} times. "
                "Use a different tool or change arguments based on prior output."
            )
        return None

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        key = (str(tool_name or ""), _args_hash(dict(args or {})))
        self._history.append(key)
        if len(self._history) > 64:
            self._history = self._history[-64:]

    def reset(self) -> None:
        self._history = []


__all__ = ["ToolCallLoopDetector"]
