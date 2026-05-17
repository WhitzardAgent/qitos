"""FSM state and event model for the canonical QitOS engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RuntimePhase(str, Enum):
    INIT = "INIT"
    DECIDE = "DECIDE"
    ACT = "ACT"
    CRITIC = "CRITIC"
    REDUCE = "REDUCE"
    CHECK_STOP = "CHECK_STOP"
    END = "END"
    DECIDE_ERROR = "DECIDE_ERROR"
    ACT_ERROR = "ACT_ERROR"
    RECOVER = "RECOVER"
    DELEGATE_START = "DELEGATE_START"
    DELEGATE_END = "DELEGATE_END"
    HANDOFF_START = "HANDOFF_START"
    HANDOFF_END = "HANDOFF_END"
    FANOUT_START = "FANOUT_START"
    FANOUT_END = "FANOUT_END"
    COMPACT = "COMPACT"
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"


@dataclass
class RuntimeBudget:
    max_steps: int = 20
    max_runtime_seconds: Optional[float] = None
    max_tokens: Optional[int] = None


@dataclass
class ContextConfig:
    enabled: bool = True
    warning_ratio: float = 0.80
    compact_ratio: float = 0.85
    target_utilization: float = 0.85
    safety_reserve_tokens: Optional[int] = None
    safety_reserve_ratio: float = 0.05
    min_safety_reserve_tokens: int = 1024
    default_context_window: int = 128000
    tool_result_max_chars: int = 50000
    tool_result_per_message_max_chars: int = 200000
    conversation_max_rounds: int = 10
    reactive_compact: bool = True
    loop_max_repeats: int = 3
    max_handoffs: int = 10
    strict_overflow: bool = True
    show_ui: bool = True


@dataclass
class ContextBudget:
    max_input_tokens: int
    target_utilization: float = 0.85
    tool_result_max_chars: int = 50000
    conversation_max_rounds: int = 10

    @property
    def target_tokens(self) -> int:
        return int(self.max_input_tokens * self.target_utilization)


@dataclass
class ContextTelemetry:
    context_window: Optional[int] = None
    available_input_budget: Optional[int] = None
    system_prompt_tokens: int = 0
    history_tokens: int = 0
    prepared_tokens: int = 0
    input_tokens_total: int = 0
    output_tokens: int = 0
    occupancy_ratio: float = 0.0
    warning_threshold_ratio: float = 0.80
    counting_mode: str = "disabled"
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    tokens_total: int = 0
    peak_input_tokens: int = 0
    peak_occupancy_ratio: float = 0.0
    history_message_count: int = 0
    compact_events: List[Dict[str, Any]] = field(default_factory=list)
    reserve_tokens: int = 0
    max_output_tokens: int = 0
    history_budget: Optional[int] = None


@dataclass
class RuntimeEvent:
    step_id: int
    phase: RuntimePhase
    ok: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class StepRecord:
    step_id: int
    phase_events: List[RuntimeEvent] = field(default_factory=list)
    observation: Any = None
    decision: Any = None
    model_response: Dict[str, Any] = field(default_factory=dict)
    actions: List[Any] = field(default_factory=list)
    action_results: List[Any] = field(default_factory=list)
    tool_invocations: List[Any] = field(default_factory=list)
    critic_outputs: List[Any] = field(default_factory=list)
    state_diff: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    prompt_metadata: Dict[str, Any] = field(default_factory=dict)
    protocol_id: Optional[str] = None
    parser_selected: Optional[str] = None
    parser_fallback_used: bool = False
    parser_attempts: List[Dict[str, Any]] = field(default_factory=list)
    parser_diagnostics: Dict[str, Any] = field(default_factory=dict)
    parser_contract: Optional[str] = None
    parser_salvage_applied: bool = False
    decision_source: Optional[str] = None
    agent_id: Optional[str] = None
    native_tool_call_used: bool = False
    native_tool_call_fallback_reason: Optional[str] = None
    visual_assets: List[Dict[str, Any]] = field(default_factory=list)
    observation_modalities: List[str] = field(default_factory=list)
    visual_asset_count: int = 0
    has_screenshot: bool = False
    has_dom: bool = False
    has_accessibility_tree: bool = False
    model_input_modalities: List[str] = field(default_factory=list)
    model_input_visual_count: int = 0


@dataclass
class StepResult:
    """Result of a single Engine step for interactive REPLs.

    Provides all data an external driver (REPL, debugger, etc.) needs
    to display output, handle permissions, and decide whether to continue.
    """

    step_id: int
    decision: Any  # Decision[ActionT]
    record: StepRecord
    observation: Any
    action_results: List[Any] = field(default_factory=list)
    stop: bool = False
    stop_reason: Optional[Any] = None  # StopReason
    error: Optional[Exception] = None
    recovered: bool = False
