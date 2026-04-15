"""Canonical tool-result contract used by Engine observations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Literal


@dataclass
class ToolResult:
    """Normalized tool execution result."""

    status: Literal["success", "error"] = "success"
    output: Any = None
    error: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def text(self) -> str:
        if isinstance(self.output, str):
            return self.output
        try:
            return json.dumps(self.output, ensure_ascii=False, default=str)
        except Exception:
            return str(self.output)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": str(self.status),
            "output": self.output,
            "error": self.error,
            "metadata": dict(self.metadata),
        }
        # Backward-compatible flattening: legacy reducers expect direct tool fields.
        if isinstance(self.output, dict):
            for key, value in self.output.items():
                if key in payload:
                    continue
                payload[str(key)] = value
        return payload

    @classmethod
    def from_value(cls, payload: Any) -> "ToolResult":
        if isinstance(payload, ToolResult):
            return payload
        if isinstance(payload, dict):
            status = str(payload.get("status") or "success")
            if status not in {"success", "error"}:
                status = "success" if not payload.get("error") else "error"
            return cls(
                status=status,  # type: ignore[arg-type]
                output=payload.get("output", payload),
                error=(
                    str(payload.get("error"))
                    if payload.get("error") not in (None, "")
                    else None
                ),
                metadata=(
                    dict(payload.get("metadata", {}))
                    if isinstance(payload.get("metadata"), dict)
                    else {}
                ),
            )
        if isinstance(payload, str):
            return cls(status="success", output=payload)
        return cls(status="success", output=payload)


__all__ = ["ToolResult"]
