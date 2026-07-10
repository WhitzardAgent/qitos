"""Trace event model for QitOS."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class TraceEvent:
    run_id: str
    step_id: int
    phase: str
    ok: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TraceStep:
    """Lightweight step summary with event references.

    Heavy data (observation, decision, model_response, actions, etc.)
    is already captured in events.jsonl as individual RuntimeEvent entries.
    This step record only keeps lightweight metadata + event index range
    so the full step can be reconstructed from events.jsonl.
    """
    step_id: int
    agent_id: Optional[str] = None
    # Event index range for reconstruction from events.jsonl
    event_start_idx: int = -1
    event_end_idx: int = -1
    # Lightweight fields not duplicated in events
    state_diff: Dict[str, Any] = field(default_factory=dict)
    protocol_id: Optional[str] = None
    parser_selected: Optional[str] = None
    parser_fallback_used: bool = False
    parser_contract: Optional[str] = None
    parser_salvage_applied: bool = False
    decision_source: Optional[str] = None
    native_tool_call_used: bool = False
    native_tool_call_fallback_reason: Optional[str] = None
    visual_asset_count: int = 0
    has_screenshot: bool = False
    has_dom: bool = False
    has_accessibility_tree: bool = False
    model_input_modalities: List[str] = field(default_factory=list)
    model_input_visual_count: int = 0
    # Retained for backward compat — but will be empty in new format.
    # Downstream consumers should use event_refs to reconstruct.
    observation: Any = None
    decision: Any = None
    model_response: Dict[str, Any] = field(default_factory=dict)
    actions: List[Any] = field(default_factory=list)
    action_results: List[Any] = field(default_factory=list)
    tool_invocations: List[Any] = field(default_factory=list)
    critic_outputs: List[Any] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    prompt_metadata: Dict[str, Any] = field(default_factory=dict)
    parser_attempts: List[Dict[str, Any]] = field(default_factory=list)
    parser_diagnostics: Dict[str, Any] = field(default_factory=dict)
    visual_assets: List[Dict[str, Any]] = field(default_factory=list)
    observation_modalities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
