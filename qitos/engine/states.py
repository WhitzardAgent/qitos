"""FSM state and event model for the canonical QitOS engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    INTERRUPT = "INTERRUPT"
    FANOUT_START = "FANOUT_START"
    FANOUT_END = "FANOUT_END"
    COMPACT = "COMPACT"
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"


@dataclass
class RuntimeBudget:
    max_steps: int = 10  # Default matches Engine's safe step limit
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
    provider_prompt_tokens: Optional[int] = None
    provider_completion_tokens: Optional[int] = None
    provider_total_tokens: Optional[int] = None
    planned_prompt_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    meter_source: str = "local_estimate"
    meter_status: str = "not_configured"
    meter_error: str = ""
    token_estimate_error: Optional[int] = None
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
    # Parser-derived actions are mirrored as OpenAI-compatible tool calls so
    # their results can remain in the same durable conversation chain.
    history_tool_calls_pending: bool = False
    visual_assets: List[Dict[str, Any]] = field(default_factory=list)
    observation_modalities: List[str] = field(default_factory=list)
    visual_asset_count: int = 0
    has_screenshot: bool = False
    has_dom: bool = False
    has_accessibility_tree: bool = False
    model_input_modalities: List[str] = field(default_factory=list)
    model_input_visual_count: int = 0


@dataclass
class CriticTrace:
    """Structured record of a single critic evaluation within a run."""

    step_id: int
    critic_name: str
    action: str  # "continue" | "stop" | "retry"
    reason: str = ""
    score: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    instruction_patch: Optional[str] = None
    state_patch: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "step_id": self.step_id,
            "critic_name": self.critic_name,
            "action": self.action,
            "reason": self.reason,
            "score": self.score,
        }
        if self.details:
            d["details"] = self.details
        if self.instruction_patch is not None:
            d["instruction_patch"] = self.instruction_patch
        if self.state_patch is not None:
            d["state_patch"] = self.state_patch
        return d


@dataclass
class HandoffTrace:
    """Structured record of an agent handoff within a run."""

    step_id: int
    from_agent: str
    to_agent: str
    context_strategy: str = ""
    messages_passed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "context_strategy": self.context_strategy,
            "messages_passed": self.messages_passed,
        }


@dataclass(frozen=True)
class EngineConfig:
    """Serializable snapshot of Engine configuration."""

    agent_name: str = ""
    model_id: str = ""
    budget_max_steps: int = 10
    budget_max_runtime_seconds: Optional[float] = None
    budget_max_tokens: Optional[int] = None
    critic_names: List[str] = field(default_factory=list)
    stop_criteria_names: List[str] = field(default_factory=list)
    has_checkpoint_store: bool = False
    has_tracing_provider: bool = False
    protocol_id: Optional[str] = None
    delegate_depth: int = 0
    has_shared_memory: bool = False
    has_env: bool = False
    tool_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    interrupt_info: Optional[Any] = None  # InterruptInfo (avoids circular import)
