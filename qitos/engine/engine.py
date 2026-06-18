"""Canonical Engine for AgentModule execution."""

from __future__ import annotations

import logging
import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, Type, TypeVar, cast
from uuid import uuid4

_logger = logging.getLogger("qitos.engine")

from ..checkpoint.store import Checkpoint, CheckpointConfig, CheckpointId, CheckpointMetadata, CheckpointStore, StateVersions
from ..checkpoint.versioning import StateVersionTracker
from ..checkpoint.durability import DurabilityManager, DurabilityMode
from ..checkpoint.pending_writes import PendingWriteManager
from ..core.agent_module import AgentModule
from ..core.decision import Decision
from ..core.errors import ErrorCategory, StopReason
from ..core.env import Env, EnvObservation, EnvStepResult
from ..core.history import History, HistoryMessage, HistoryPolicy
from ..core.interceptor import InterceptorChain, ToolInterceptor
from ..core.memory import Memory, MemoryRecord
from ..core.state import StateSchema
from ..core.task import Task, TaskResult, TaskValidationIssue
from ..core.tool_result import ToolResult
from ..trace import TraceWriter
from ..protocols import get_protocol, infer_protocol_from_parser
from ..models.profile_registry import infer_default_protocol, infer_model_profile
from ._action_runtime import _ActionRuntime
from ._context_runtime import _ContextRuntime
from ._control_runtime import _ControlRuntime
from ._env_runtime import _EnvRuntime
from ._loop_detector import ToolCallLoopDetector
from ._model_runtime import _ModelRuntime
from ._handoff_runtime import _HandoffRuntime
from ._trace_runtime import _TraceRuntime
from .action_executor import ActionExecutor
from .cancellation import CancelMode, CancelToken
from .branching import BranchSelector, FirstCandidateSelector
from .critic import Critic
from .hooks import EngineHook, HookContext
from .parser import Parser
from .recovery import RecoveryPolicy, build_failure_report
from .search import Search
from .states import (
    ContextConfig,
    CriticTrace,
    EngineConfig,
    HandoffTrace,
    RuntimeBudget,
    RuntimeEvent,
    RuntimePhase,
    StepRecord,
    StepResult,
)
from .stop_criteria import FinalResultCriteria, StopCriteria
from .validation import StateValidationGate


StateT = TypeVar("StateT", bound=StateSchema)
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")

RecoveryHandler = Callable[[StateT, RuntimePhase, Exception], None]


class _EngineWindowHistory(History):
    def __init__(self, window_size: int = 24):
        self.window_size = int(window_size)
        self._items: List[HistoryMessage] = []

    def append(self, message: HistoryMessage) -> None:
        self._items.append(message)
        self.evict()

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> Any:
        query = query or {}
        max_items = int(
            query.get(
                "max_items",
                self.window_size if self.window_size > 0 else len(self._items),
            )
        )
        roles = query.get("roles")
        step_min = query.get("step_min")
        step_max = query.get("step_max")
        items = list(self._items)
        if roles:
            role_set = set(roles)
            items = [x for x in items if x.role in role_set]
        if step_min is not None:
            items = [x for x in items if x.step_id >= int(step_min)]
        if step_max is not None:
            items = [x for x in items if x.step_id <= int(step_max)]
        if max_items > 0:
            items = items[-max_items:]
        return items

    def summarize(self, max_items: int = 5) -> str:
        items = self.retrieve(query={"max_items": max_items})
        if not isinstance(items, list):
            return ""
        return "\n".join(
            f"[{x.step_id}] {x.role}: {str(x.content)[:120]}"
            for x in items
            if isinstance(x, HistoryMessage)
        )

    def evict(self) -> int:
        if self.window_size <= 0 or len(self._items) <= self.window_size:
            return 0
        removed = len(self._items) - self.window_size
        self._items = self._items[-self.window_size :]
        return removed

    def reset(self, run_id: Optional[str] = None) -> None:
        self._items = []


@dataclass
class StepSummary:
    step_id: int
    tool_name: str
    status: str
    latency_ms: float = 0.0
    error: Optional[str] = None
    result_preview: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "result_preview": self.result_preview,
        }


@dataclass
class EngineResult(Generic[StateT]):
    state: StateT
    records: List[StepRecord]
    events: List[RuntimeEvent]
    step_count: int
    task_result: Optional[TaskResult] = None
    runtime_seconds: float = 0.0
    total_tokens: int = 0
    run_id: str = ""
    critic_traces: List[CriticTrace] = field(default_factory=list)
    handoff_traces: List[HandoffTrace] = field(default_factory=list)
    _cancel_token: Optional[CancelToken] = None

    def cancel(self, mode: str = "immediate") -> None:
        """Request cancellation of the running Engine.

        Parameters
        ----------
        mode : str
            ``"immediate"`` — stop as soon as possible (may be mid-step).
            ``"after_step"`` — wait for the current step to complete first.
        """
        if self._cancel_token is None:
            return
        self._cancel_token.request_cancel(mode)

    @property
    def tool_calls_by_name(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records:
            for inv in list(getattr(record, "tool_invocations", []) or []):
                if not isinstance(inv, dict):
                    continue
                name = str(inv.get("tool_name", "") or "").strip()
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + 1
        return counts

    @property
    def success_rate(self) -> float:
        total = 0
        success = 0
        for record in self.records:
            for item in list(getattr(record, "action_results", []) or []):
                total += 1
                if ToolResult.from_value(item).is_success:
                    success += 1
        if total <= 0:
            return 0.0
        return float(success) / float(total)

    @property
    def step_summaries(self) -> List[StepSummary]:
        items: List[StepSummary] = []
        for record in self.records:
            invocations = list(getattr(record, "tool_invocations", []) or [])
            action_results = list(getattr(record, "action_results", []) or [])
            for idx, invocation in enumerate(invocations):
                tool_name = ""
                latency_ms = 0.0
                if isinstance(invocation, dict):
                    tool_name = str(invocation.get("tool_name", "") or "")
                    latency = invocation.get("latency_ms")
                    if isinstance(latency, (int, float)):
                        latency_ms = float(latency)
                tool_result = (
                    ToolResult.from_value(action_results[idx])
                    if idx < len(action_results)
                    else ToolResult(status="error", error="missing_action_result")
                )
                preview = tool_result.text
                items.append(
                    StepSummary(
                        step_id=record.step_id,
                        tool_name=tool_name,
                        status=tool_result.status,
                        latency_ms=latency_ms,
                        error=tool_result.error,
                        result_preview=preview[:200],
                    )
                )
        return items

    def to_dict(self) -> Dict[str, Any]:
        task_result_dict: Any = None
        if self.task_result is not None:
            if hasattr(self.task_result, "to_dict"):
                task_result_dict = self.task_result.to_dict()
            else:
                task_result_dict = self.task_result
        return {
            "step_count": self.step_count,
            "runtime_seconds": self.runtime_seconds,
            "total_tokens": self.total_tokens,
            "tool_calls_by_name": self.tool_calls_by_name,
            "success_rate": self.success_rate,
            "step_summaries": [item.to_dict() for item in self.step_summaries],
            "critic_traces": [ct.to_dict() for ct in self.critic_traces],
            "handoff_traces": [ht.to_dict() for ht in self.handoff_traces],
            "task_result": task_result_dict,
            "state": self.state.to_dict() if hasattr(self.state, "to_dict") else self.state,
        }


class Engine(Generic[StateT, ObservationT, ActionT]):
    """Single execution kernel for all AgentModule workflows."""

    def __init__(
        self,
        agent: AgentModule[StateT, ObservationT, ActionT],
        agent_registry: Optional[Any] = None,
        budget: Optional[RuntimeBudget] = None,
        delegate_depth: int = 0,
        shared_memory: Any = None,
        validation_gate: Optional[StateValidationGate] = None,
        recovery_handler: Optional[RecoveryHandler] = None,
        recovery_policy: Optional[RecoveryPolicy] = None,
        trace_writer: Optional[TraceWriter] = None,
        parser: Optional[Parser[ActionT]] = None,
        protocol: Any = None,
        stop_criteria: Optional[List[StopCriteria]] = None,
        branch_selector: Optional[BranchSelector[StateT, ObservationT, ActionT]] = None,
        search: Optional[Search[StateT, ObservationT, ActionT]] = None,
        critics: Optional[List[Critic]] = None,
        env: Optional[Env] = None,
        history_policy: Optional[HistoryPolicy] = None,
        hooks: Optional[List[EngineHook]] = None,
        render_hooks: Optional[List[Any]] = None,
        context_config: Optional[ContextConfig | Dict[str, Any]] = None,
        cache_backend: Optional[Any] = None,
        checkpoint_manager: Optional[Any] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        checkpoint_durability: DurabilityMode = DurabilityMode.SYNC,
        permission_pipeline: Optional[Any] = None,
        read_before_write_enforcer: Optional[Any] = None,
        permission_interaction_callback: Optional[Any] = None,
        loop_detector: Optional[ToolCallLoopDetector] = None,
        tracing_provider: Optional[Any] = None,
        interceptors: Optional[List[ToolInterceptor]] = None,
        auto_approve: bool = False,
    ):
        self.agent = agent
        self.agent_registry = agent_registry
        self._delegate_depth = delegate_depth
        self._shared_memory = shared_memory
        self.tool_registry = agent.tool_registry
        # Ensure Engine always has a ToolRegistry — agents without tools still
        # need one for handoff/permission tools registered by the Engine itself.
        if self.tool_registry is None:
            from ..core.tool_registry import ToolRegistry as _TR
            self.tool_registry = _TR()
        self.budget = budget or RuntimeBudget()
        self._base_budget = RuntimeBudget(
            max_steps=self.budget.max_steps,
            max_runtime_seconds=self.budget.max_runtime_seconds,
            max_tokens=self.budget.max_tokens,
        )
        self.validation_gate = validation_gate or StateValidationGate()
        self.recovery_handler = recovery_handler
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.trace_writer = trace_writer
        self.parser = parser
        self.protocol = protocol
        self._resolved_protocol: Any = None
        self._resolved_protocol_source: str = ""
        self.branch_selector = branch_selector or FirstCandidateSelector()
        self.search = search
        self.critics = critics or []
        self.env = env
        self.history_policy = history_policy or HistoryPolicy()
        self.context_config = (
            context_config
            if isinstance(context_config, ContextConfig)
            else ContextConfig(**dict(context_config or {}))
        )
        self.hooks: List[Any] = list(hooks or [])
        if render_hooks:
            self.hooks.extend(render_hooks)
        if stop_criteria is None:
            self._uses_default_stop_criteria = True
            self.stop_criteria: List[StopCriteria] = [FinalResultCriteria()]
        else:
            self._uses_default_stop_criteria = False
            self.stop_criteria = list(stop_criteria)

        # Wire permission pipeline and RBW enforcer: explicit params > agent attrs
        resolved_pipeline = permission_pipeline or getattr(agent, "permission_pipeline", None)
        resolved_rbw = read_before_write_enforcer or getattr(agent, "_rbw_enforcer", None)

        # Build interceptor chain from interceptors list
        self._interceptor_chain: Optional[InterceptorChain] = None
        all_interceptors: List[ToolInterceptor] = list(interceptors or [])
        self._delegate_interceptor: Optional[Any] = None
        if agent_registry is not None:
            from .interceptors import DelegateEventInterceptor
            self._delegate_interceptor = DelegateEventInterceptor(event_sink=None)
            all_interceptors.append(self._delegate_interceptor)
        if all_interceptors:
            self._interceptor_chain = InterceptorChain(all_interceptors)

        self.auto_approve = auto_approve
        self.executor = (
            ActionExecutor(
                tool_registry=self.tool_registry,
                trace_writer=self.trace_writer,
                delegate_depth=self._delegate_depth,
                shared_memory=self._shared_memory,
                engine=self,
                permission_pipeline=resolved_pipeline,
                read_before_write_enforcer=resolved_rbw,
                permission_interaction_callback=permission_interaction_callback,
                interceptor_chain=self._interceptor_chain,
                auto_approve=auto_approve,
            )
            if self.tool_registry is not None
            else None
        )
        self.events: List[RuntimeEvent] = []
        self.records: List[StepRecord] = []
        self._active_state: Optional[StateT] = None
        self._active_task: str = ""
        self._active_task_obj: Optional[Task] = None
        self._last_env_observation: Optional[EnvObservation] = None
        self._last_env_result: Optional[EnvStepResult] = None
        self._token_usage: int = 0
        self._active_run_id: str = ""
        self._runtime_history: History = _EngineWindowHistory(window_size=24)
        self._tool_loop_detector = loop_detector or ToolCallLoopDetector(
            max_repeats=max(1, int(self.context_config.loop_max_repeats))
        )
        self._last_system_prompt: str = ""
        self._critic_modified_prompt: Optional[str] = None
        self._critic_instruction_patch: Optional[str] = None
        self._last_prompt_metadata: Dict[str, Any] = {}
        self._last_context_telemetry: Dict[str, Any] = {}
        self._model_runtime: _ModelRuntime[StateT, ObservationT, ActionT] = (
            _ModelRuntime(self)
        )
        self._action_runtime: _ActionRuntime[StateT, ActionT] = _ActionRuntime(self)
        self._env_runtime: _EnvRuntime[StateT, ObservationT, ActionT] = _EnvRuntime(
            self
        )
        self._control_runtime: _ControlRuntime[StateT, ObservationT, ActionT] = (
            _ControlRuntime(self)
        )
        self._trace_runtime: _TraceRuntime[StateT] = _TraceRuntime(self)
        self._handoff_runtime: _HandoffRuntime[StateT, ObservationT, ActionT] = (
            _HandoffRuntime(self)
        )
        self._handoff_history: list[str] = []  # tracks agent names for loop detection
        # NOTE (v0.6): Handoff Decision-mode handling is stable for v0.6.
        # Changes to the Engine loop for full handoff context strategies,
        # shared memory, and canonical multi-agent templates are deferred to v0.7.
        # See docs/internal/plans/v0.7_handoff_scope.md for details.
        self.stream_callback: Optional[Any] = None  # Callable[[str], None] for streaming
        self._context_runtime = _ContextRuntime(self)
        self._context_runtime.apply_config(self.context_config)

        # LLM Cache: auto-wrap agent.llm with CachedModel if backend provided
        self.cache_backend = cache_backend
        if self.cache_backend is not None and getattr(self.agent, "llm", None) is not None:
            from ..cache import CachedModel

            if not isinstance(self.agent.llm, CachedModel):
                self.agent.llm = CachedModel(self.agent.llm, self.cache_backend)

        # Checkpoint: new CheckpointStore takes precedence over legacy CheckpointManager
        self._checkpoint_store = checkpoint_store
        self._version_tracker: Optional[StateVersionTracker] = None
        self._durability_manager: Optional[DurabilityManager] = None
        self._pending_write_manager: Optional[PendingWriteManager] = None
        self._last_checkpoint_id: Optional[CheckpointId] = None

        if checkpoint_store is not None:
            self._durability_manager = DurabilityManager(checkpoint_store, mode=checkpoint_durability)
            self._pending_write_manager = PendingWriteManager(checkpoint_store)

        # Legacy checkpoint manager (deprecated — kept for backward compat)
        self.checkpoint_manager = checkpoint_manager

        # Tracing provider: if provided, bridge legacy TraceWriter to it
        self._tracing_provider = tracing_provider
        if tracing_provider is not None and trace_writer is not None:
            from ..tracing.legacy_processor import LegacyTraceWriterProcessor
            tracing_provider.add_processor(LegacyTraceWriterProcessor(trace_writer))

        # Handoff tools: auto-register if agent declares handoff_targets
        self._handoff_tools: List[Any] = []
        if getattr(agent, "handoff_targets", None) and self.tool_registry is not None:
            self._register_handoff_tools()

        # Cancellation token — shared with EngineResult for external cancel
        self._cancel_token = CancelToken()

    def resolve_protocol(self) -> Any:
        if self._resolved_protocol is not None:
            return self._resolved_protocol
        explicit = self.protocol
        if explicit is not None:
            self._resolved_protocol = get_protocol(explicit)
            self._resolved_protocol_source = "run_protocol"
            return self._resolved_protocol
        agent_protocol = getattr(self.agent, "model_protocol", None)
        if agent_protocol is not None:
            self._resolved_protocol = get_protocol(agent_protocol)
            self._resolved_protocol_source = "agent_model_protocol"
            return self._resolved_protocol
        parser = self.parser or getattr(self.agent, "model_parser", None)
        if parser is not None:
            inferred = infer_protocol_from_parser(parser)
            if inferred is not None:
                self._resolved_protocol = inferred
                self._resolved_protocol_source = "parser_inferred"
                return self._resolved_protocol
        llm = getattr(self.agent, "llm", None)
        model_name = getattr(llm, "model", None) or getattr(llm, "model_name", None)
        default_protocol = infer_default_protocol(model_name, fallback="react_text_v1")
        self._resolved_protocol = get_protocol(default_protocol)
        self._resolved_protocol_source = (
            "model_profile"
            if infer_model_profile(model_name) is not None
            else "framework_default"
        )
        return self._resolved_protocol

    def register_hook(self, hook: Any) -> None:
        """Register one runtime hook instance."""
        self.hooks.append(hook)

    def unregister_hook(self, hook: Any) -> None:
        """Unregister one runtime hook instance if present."""
        self.hooks = [h for h in self.hooks if h is not hook]

    def clear_hooks(self) -> None:
        """Remove all runtime hooks."""
        self.hooks = []

    # ------------------------------------------------------------------
    # Public step-by-step API for interactive REPLs and external drivers
    # ------------------------------------------------------------------

    def init_session(self, task: str, **kwargs: Any) -> tuple[StateT, ObservationT]:
        """Initialize a new session for step-by-step execution.

        Sets up Engine run state, creates initial state and observation.
        Returns (state, observation) ready for the first ``step()`` call.
        """
        self._reset_run_state()
        memory = self._memory()
        if memory is not None:
            try:
                memory.reset()
            except Exception as exc:
                _logger.debug("Failed to reset memory: %s", exc)
        try:
            self._history().reset()
        except Exception as exc:
            _logger.debug("Failed to reset history: %s", exc)
        if hasattr(self.recovery_policy, "reset"):
            try:
                self.recovery_policy.reset()
            except Exception as exc:
                _logger.debug("Failed to reset recovery_policy: %s", exc)
        self._active_run_id = f"run_{uuid4().hex[:12]}"
        self._last_system_prompt = ""
        self._last_prompt_metadata = {}
        self._token_usage = 0
        self._last_context_telemetry = {}
        self._context_runtime.reset()
        self._resolved_protocol = self.resolve_protocol()

        task_obj, task_text = self._normalize_task(task)
        self._apply_task_budget(task_obj)

        state = self.agent.init_state(task_text, **kwargs)
        self._memory_append("task", {"objective": task_text}, 0)
        self._active_task = task_text
        self._active_task_obj = task_obj
        self._active_state = state

        self._setup_toolsets(
            {"state": state, "trace_writer": self.trace_writer, "task": task_obj or task_text}
        )

        started_at = time.monotonic()
        observation = self._build_initial_observation(state, step_id=0, started_at=started_at)
        return state, observation

    def step(
        self,
        state: StateT,
        observation: ObservationT,
    ) -> StepResult:
        """Execute a single decide → act → reduce step.

        Returns a :class:`StepResult` with the decision, action results,
        new observation, and stop status.

        The caller can inspect ``result.stop`` to decide whether to
        continue the loop.
        """
        from .interrupt import (
            EngineInterrupt,
            InterruptInfo,
            _reset_interrupt_context,
        )

        _reset_interrupt_context()
        step_id = state.current_step
        started_at = time.monotonic()
        record = StepRecord(step_id=step_id, agent_id=self.agent.name)
        self.records.append(record)

        # DECIDE
        try:
            decision = self._run_decide(state, observation, record)
        except EngineInterrupt as ei:
            # Save checkpoint and report interrupt
            cp_id = self._save_interrupt_checkpoint(step_id, state, ei)
            info = InterruptInfo(
                interrupt_id=ei.interrupt_id,
                checkpoint_id=cp_id,
                value=ei.value,
            )
            self._emit(step_id, RuntimePhase.INTERRUPT, ok=True, payload={"interrupt_id": ei.interrupt_id})
            self._finalize_step(record, state)
            return StepResult(
                step_id=step_id,
                decision=None,
                record=record,
                observation=observation,
                action_results=[],
                stop=True,
                stop_reason=StopReason.INTERRUPT,
                interrupt_info=info,
            )
        except Exception as exc:
            failed_phase = self._infer_failed_phase(record)
            if self._recover(state, failed_phase, exc):
                self._finalize_step(record, state)
                return StepResult(
                    step_id=step_id,
                    decision=None,
                    record=record,
                    observation=observation,
                    action_results=[],
                    stop=False,
                    recovered=True,
                )
            self._finalize_step(record, state)
            return StepResult(
                step_id=step_id,
                decision=None,
                record=record,
                observation=observation,
                action_results=[],
                stop=True,
                stop_reason=StopReason.UNRECOVERABLE_ERROR,
                error=exc,
            )

        # Handle non-act modes directly
        if decision.mode in ("final", "wait", "handoff"):
            if decision.mode == "final":
                new_observation = self._build_observation_after_action(
                    state, step_id, started_at, decision, []
                )
                record.observation = new_observation
                self._memory_append("observation", new_observation, record.step_id)
                self._run_reduce(state, new_observation, decision, record)
                stop = self._run_check_stop(state, decision, step_id, started_at)
                self._finalize_step(record, state)
                return StepResult(
                    step_id=step_id,
                    decision=decision,
                    record=record,
                    observation=new_observation,
                    action_results=[],
                    stop=stop,
                    stop_reason=(
                        StopReason(state.stop_reason)
                        if state.stop_reason
                        else StopReason.FINAL
                    ),
                )
            self._finalize_step(record, state)
            return StepResult(
                step_id=step_id,
                decision=decision,
                record=record,
                observation=observation,
                action_results=[],
                stop=(decision.mode == "final"),
                stop_reason=StopReason.FINAL if decision.mode == "final" else None,
            )

        # ACT
        try:
            action_results = self._run_act(state, decision, record)
        except Exception as exc:
            failed_phase = self._infer_failed_phase(record)
            if self._recover(state, failed_phase, exc):
                self._finalize_step(record, state)
                return StepResult(
                    step_id=step_id,
                    decision=decision,
                    record=record,
                    observation=observation,
                    action_results=[],
                    stop=False,
                    recovered=True,
                )
            self._finalize_step(record, state)
            return StepResult(
                step_id=step_id,
                decision=decision,
                record=record,
                observation=observation,
                action_results=[],
                stop=True,
                stop_reason=StopReason.UNRECOVERABLE_ERROR,
                error=exc,
            )

        # REDUCE
        new_observation = self._build_observation_after_action(
            state, step_id, started_at, decision, action_results
        )
        record.observation = new_observation
        self._memory_append("observation", new_observation, record.step_id)
        try:
            self._run_reduce(state, new_observation, decision, record)
        except EngineInterrupt as ei:
            cp_id = self._save_interrupt_checkpoint(step_id, state, ei)
            info = InterruptInfo(
                interrupt_id=ei.interrupt_id,
                checkpoint_id=cp_id,
                value=ei.value,
            )
            self._emit(step_id, RuntimePhase.INTERRUPT, ok=True, payload={"interrupt_id": ei.interrupt_id})
            self._finalize_step(record, state)
            return StepResult(
                step_id=step_id,
                decision=decision,
                record=record,
                observation=new_observation,
                action_results=action_results,
                stop=True,
                stop_reason=StopReason.INTERRUPT,
                interrupt_info=info,
            )

        # CHECK STOP
        stop = self._run_check_stop(state, decision, step_id, started_at)
        self._finalize_step(record, state)

        stop_reason = None
        if stop:
            stop_reason = StopReason(state.stop_reason) if state.stop_reason else StopReason.MAX_STEPS

        return StepResult(
            step_id=step_id,
            decision=decision,
            record=record,
            observation=new_observation,
            action_results=action_results,
            stop=stop,
            stop_reason=stop_reason,
        )

    def advance_step(self, state: StateT) -> None:
        """Advance the state step counter after a completed step."""
        state.advance_step()

    def append_user_message(self, content: str, step_id: int) -> None:
        """Append a user message to the conversation history."""
        self._history_append("user", content, step_id, metadata={"source": "user"})

    def submit_turn(
        self, state: StateT, user_message: str
    ) -> tuple[StateT, ObservationT]:
        """Submit a user message and build the initial observation for the next turn.

        This is the public API for multi-turn REPLs. It wraps
        ``append_user_message()`` + ``_build_initial_observation()`` so
        callers don't need to reach into private methods.

        Returns (state, observation) ready for the next ``step()`` call.
        """
        step_id = state.current_step
        self.append_user_message(user_message, step_id)
        observation = self._build_initial_observation(state, step_id, time.monotonic())
        return state, observation

    def execute_actions(
        self, state: StateT, decision: Decision[ActionT], record: StepRecord
    ) -> List[Any]:
        """Public alias for ``_run_act()`` — execute a decision's actions.

        Useful for REPLs that want to handle DECIDE themselves but delegate
        ACT execution to the engine.
        """
        return self._run_act(state, decision, record)

    def rebuild_observation(self, state: StateT) -> ObservationT:
        """Build a fresh observation for the current state.

        Useful after error recovery or parser repair when the REPL needs
        to continue the loop without submitting a new user message.
        """
        return self._build_initial_observation(
            state, state.current_step, time.monotonic()
        )

    def budget_exhausted(self, state: StateT) -> bool:
        """Check if the runtime budget has been exhausted."""
        return self._budget_exhausted(state.current_step, time.monotonic(), state)

    @property
    def current_state(self) -> Optional[StateT]:
        """Return the active state, if any."""
        return self._active_state

    @property
    def checkpoint_store(self) -> Optional[CheckpointStore]:
        """Return the configured CheckpointStore, if any."""
        return self._checkpoint_store

    @property
    def tracing_provider(self) -> Any:
        """Return the configured TracingProvider, if any."""
        return self._tracing_provider

    def run(self, task: str | Task, **kwargs: Any) -> EngineResult[StateT]:
        # Check for resume-from-checkpoint internal kwargs
        _resume_state = kwargs.pop("_resume_state", None)
        _resume_step = kwargs.pop("_resume_step", None)

        self._reset_run_state()
        # Wire delegate interceptor to engine's event list
        if self._delegate_interceptor is not None:
            self._delegate_interceptor._event_sink = self.events
        memory = self._memory()
        if memory is not None:
            try:
                memory.reset()
            except Exception as exc:
                _logger.debug("Failed to reset memory: %s", exc)
        try:
            self._history().reset()
        except Exception as exc:
            _logger.debug("Failed to reset history: %s", exc)
        if hasattr(self.recovery_policy, "reset"):
            try:
                self.recovery_policy.reset()
            except Exception as exc:
                _logger.debug("Failed to reset recovery_policy: %s", exc)
        self._active_run_id = (
            str(getattr(self.trace_writer, "run_id", "")).strip()
            if self.trace_writer is not None
            else ""
        ) or f"run_{uuid4().hex[:12]}"
        self._last_system_prompt = ""
        self._last_prompt_metadata = {}
        task_obj, task_text = self._normalize_task(task)
        self._apply_task_budget(task_obj)
        self._token_usage = 0
        self._last_context_telemetry = {}
        self._context_runtime.reset()
        self._resolved_protocol = self.resolve_protocol()
        # Record multi-agent topology in trace metadata
        if self.trace_writer is not None and self.agent_registry is not None:
            self.trace_writer.metadata["agent_topology"] = {
                "type": "multi_agent",
                "agents": [s.name for s in self.agent_registry.list_available()],
            }
            self.trace_writer.metadata["agent_name"] = self.agent.name

        # Initialize version tracker for checkpoint store
        if self._checkpoint_store is not None:
            self._version_tracker = StateVersionTracker()

        # Connect MCP servers and bridge their tools
        self._connected_mcp_servers: List[Any] = []
        if getattr(self.agent, "mcp_servers", None):
            self._connect_mcp_servers()

        # State initialization: fresh or resumed
        if _resume_state is not None:
            state = _resume_state
        else:
            state = self.agent.init_state(task_text, **kwargs)
        self._memory_append(
            "task",
            {
                "objective": task_text,
                "task_id": task_obj.id if task_obj is not None else None,
            },
            0,
        )
        self._active_task = task_text
        self._active_task_obj = task_obj
        self._active_state = state
        started_at = time.monotonic()
        self._hydrate_trace_metadata(task_obj=task_obj, task_text=task_text)

        self._setup_toolsets(
            {
                "state": state,
                "trace_writer": self.trace_writer,
                "task": task_obj or task_text,
            }
        )
        self._setup_env(task_obj=task_obj, state=state, kwargs=kwargs)
        harness_diagnostics = self._harness_mismatch_diagnostics()
        self._emit(
            0,
            RuntimePhase.INIT,
            payload={
                "task": task_text,
                "task_id": task_obj.id if task_obj is not None else None,
                "task_meta": self._task_meta(task_obj),
                "run_meta": self._run_meta(),
                "env": self._env_identity(),
                "harness_diagnostics": harness_diagnostics,
            },
        )
        if harness_diagnostics.get("mismatch"):
            self._emit(
                0,
                RuntimePhase.INIT,
                payload={"stage": "harness_mismatch", **harness_diagnostics},
            )
        self._notify_run_start(task_text, state)
        preflight_issues = self._preflight_validate(
            task_obj=task_obj, workspace=kwargs.get("workspace")
        )
        if preflight_issues:
            has_task_issue = any(
                not issue.code.startswith("ENV_") for issue in preflight_issues
            )
            stop_reason = (
                StopReason.TASK_VALIDATION_FAILED
                if has_task_issue
                else StopReason.ENV_CAPABILITY_MISMATCH
            )
            state.set_stop(stop_reason)
            state.final_result = "Preflight validation failed."
            self._emit(
                0,
                RuntimePhase.END,
                ok=False,
                payload={
                    "stop_reason": state.stop_reason,
                    "error_category": (
                        ErrorCategory.TASK.value
                        if has_task_issue
                        else ErrorCategory.ENV.value
                    ),
                    "issues": [self._task_issue_to_dict(x) for x in preflight_issues],
                },
            )
            result = EngineResult(
                state=state,
                records=self.records,
                events=self.events,
                step_count=0,
                task_result=self._build_task_result(
                    state, task_obj=task_obj, started_at=started_at
                ),
                runtime_seconds=time.monotonic() - started_at,
                total_tokens=int(self._token_usage),
                run_id=self._active_run_id,
                _cancel_token=self._cancel_token,
            )
            self._notify_run_end(result)
            self._clear_active_context()
            self._teardown_env()
            self._teardown_toolsets(
                {
                    "state": state,
                    "trace_writer": self.trace_writer,
                    "task": task_obj or task_text,
                }
            )
            return result

        step_id = _resume_step if _resume_step is not None else 0
        current_observation = self._build_initial_observation(
            state, step_id, started_at
        )
        try:
            while True:
                # -- Cancellation check --
                if self._cancel_token.is_cancel_requested:
                    if self._cancel_token.mode == CancelMode.IMMEDIATE:
                        self._emit(
                            step_id,
                            RuntimePhase.END,
                            ok=False,
                            payload={"stop_reason": "cancelled_immediate"},
                        )
                        break
                    # after_step: break after this iteration's step completes
                    # (checked again at end of loop body below)

                if self._budget_exhausted(step_id, started_at, state):
                    self._emit(
                        step_id,
                        RuntimePhase.END,
                        ok=False,
                        payload={"stop_reason": state.stop_reason},
                    )
                    break

                self.validation_gate.before_phase(state, RuntimePhase.DECIDE.value)

                record = StepRecord(step_id=step_id, agent_id=self.agent.name)
                self.records.append(record)

                self._dispatch_hook(
                    "on_before_step",
                    HookContext(
                        task=task_text,
                        step_id=step_id,
                        phase=RuntimePhase.DECIDE,
                        state=state,
                        observation=current_observation,
                        record=record,
                    ),
                )
                try:
                    decision = self._run_decide(state, current_observation, record)
                except Exception as exc:
                    failed_phase = self._infer_failed_phase(record)
                    if not self._recover(state, failed_phase, exc):
                        self._finalize_step(record, state)
                        self._emit(
                            step_id,
                            RuntimePhase.END,
                            ok=False,
                            payload={"stop_reason": state.stop_reason},
                        )
                        break
                    self._finalize_step(record, state)
                    self._dispatch_hook(
                        "on_after_step",
                        HookContext(
                            task=task_text,
                            step_id=step_id,
                            phase=RuntimePhase.RECOVER,
                            state=state,
                            record=record,
                            stop_reason=state.stop_reason,
                        ),
                    )
                    current_observation = self._build_initial_observation(
                        state, step_id + 1, started_at
                    )
                    state.advance_step()
                    step_id += 1
                    continue

                # Handoff: swap agent within the same loop, skip act/reduce/critic
                if decision.mode == "handoff":
                    current_observation = self._execute_handoff_step(
                        state, decision, record, step_id, task_text,
                    )
                    state.advance_step()
                    step_id += 1
                    continue

                # Wait: agent requests a pause, skip act/reduce
                if (
                    decision.mode == "wait"
                    and not bool(decision.meta.get("task_complete_requested"))
                    and not bool(decision.meta.get("parser_error"))
                ):
                    self._finalize_step(record, state)
                    self._dispatch_hook(
                        "on_after_step",
                        HookContext(
                            task=task_text,
                            step_id=step_id,
                            phase=RuntimePhase.CHECK_STOP,
                            state=state,
                            record=record,
                        ),
                    )
                    current_observation = self._build_initial_observation(
                        state, step_id + 1, started_at
                    )
                    state.advance_step()
                    step_id += 1
                    continue

                # Final decisions still flow through reduce, critics, and
                # check-stop so hooks, memory, checkpoints, and agent-specific
                # finalization all see the same lifecycle as action steps.
                if (
                    decision.mode == "final"
                    or (
                        decision.mode == "wait"
                        and (
                            bool(decision.meta.get("task_complete_requested"))
                            or bool(decision.meta.get("parser_error"))
                        )
                    )
                ):
                    try:
                        action_results: List[Any] = []
                        observation = self._build_observation_after_action(
                            state=state,
                            step_id=step_id,
                            started_at=started_at,
                            decision=decision,
                            action_results=action_results,
                        )
                        record.observation = observation
                        self._memory_append("observation", observation, record.step_id)
                        self._run_reduce(state, observation, decision, record)
                        fr = getattr(state, "final_result", None)
                        if isinstance(fr, str) and fr and state.stop_reason is None:
                            state.set_stop("final", fr)
                    except Exception as exc:
                        failed_phase = self._infer_failed_phase(record)
                        if not self._recover(state, failed_phase, exc):
                            self._finalize_step(record, state)
                            self._emit(
                                step_id,
                                RuntimePhase.END,
                                ok=False,
                                payload={"stop_reason": state.stop_reason},
                            )
                            break
                        self._finalize_step(record, state)
                        self._dispatch_hook(
                            "on_after_step",
                            HookContext(
                                task=task_text,
                                step_id=step_id,
                                phase=RuntimePhase.RECOVER,
                                state=state,
                                record=record,
                                stop_reason=state.stop_reason,
                            ),
                        )
                        current_observation = self._build_initial_observation(
                            state, step_id + 1, started_at
                        )
                        state.advance_step()
                        step_id += 1
                        continue
                else:
                    try:
                        action_results = self._run_act(state, decision, record)
                        # HandoffTool interception: if a tool result signals handoff,
                        # convert to Decision.handoff() and execute the handoff flow
                        handoff_decision = self._check_handoff_from_tool_result(action_results)
                        if handoff_decision is not None:
                            current_observation = self._execute_handoff_step(
                                state, handoff_decision, record, step_id, task_text,
                            )
                            state.advance_step()
                            step_id += 1
                            continue
                        observation = self._build_observation_after_action(
                            state=state,
                            step_id=step_id,
                            started_at=started_at,
                            decision=decision,
                            action_results=action_results,
                        )
                        record.observation = observation
                        self._memory_append("observation", observation, record.step_id)
                        self._run_reduce(state, observation, decision, record)
                        # Early exit: if reduce set final_result, set stop reason
                        # so critics don't override it and FinalResultCriteria catches it
                        fr = getattr(state, 'final_result', None)
                        if isinstance(fr, str) and fr and state.stop_reason is None:
                            state.set_stop("final", fr)
                    except Exception as exc:
                        failed_phase = self._infer_failed_phase(record)
                        if not self._recover(state, failed_phase, exc):
                            self._finalize_step(record, state)
                            self._emit(
                                step_id,
                                RuntimePhase.END,
                                ok=False,
                                payload={"stop_reason": state.stop_reason},
                            )
                            break
                        self._finalize_step(record, state)
                        self._dispatch_hook(
                            "on_after_step",
                            HookContext(
                                task=task_text,
                                step_id=step_id,
                                phase=RuntimePhase.RECOVER,
                                state=state,
                                record=record,
                                stop_reason=state.stop_reason,
                            ),
                        )
                        current_observation = self._build_initial_observation(
                            state, step_id + 1, started_at
                        )
                        state.advance_step()
                        step_id += 1
                        continue

                critic_result = self._apply_critics(state, record)
                # Support both legacy str return and new dict return
                if isinstance(critic_result, str):
                    critic_result = {"action": critic_result, "modified_prompt": None, "instruction_patch": None, "state_patch": None}
                critic_action = critic_result["action"]
                if critic_action == "stop":
                    state.set_stop(StopReason.CRITIC_STOP)
                    self._finalize_step(record, state)
                    self._dispatch_hook(
                        "on_after_step",
                        HookContext(
                            task=task_text,
                            step_id=step_id,
                            phase=RuntimePhase.CRITIC,
                            state=state,
                            record=record,
                            stop_reason=state.stop_reason,
                        ),
                    )
                    self._emit(
                        step_id,
                        RuntimePhase.END,
                        payload={"stop_reason": state.stop_reason},
                    )
                    break
                if critic_action == "retry":
                    # Apply patches from critic if provided
                    self._apply_critic_patches(state, critic_result)
                    self._finalize_step(record, state)
                    self._dispatch_hook(
                        "on_after_step",
                        HookContext(
                            task=task_text,
                            step_id=step_id,
                            phase=RuntimePhase.CRITIC,
                            state=state,
                            record=record,
                        ),
                    )
                    current_observation = observation
                    state.advance_step()
                    step_id += 1
                    continue

                stop = self._run_check_stop(state, record.decision, step_id, started_at)

                self.validation_gate.after_phase(state, RuntimePhase.CHECK_STOP.value)
                self._finalize_step(record, state)
                self._save_checkpoint_if_needed(step_id, state, task_text, task_obj)
                self._dispatch_hook(
                    "on_after_step",
                    HookContext(
                        task=task_text,
                        step_id=step_id,
                        phase=RuntimePhase.CHECK_STOP,
                        state=state,
                        record=record,
                        stop_reason=state.stop_reason,
                    ),
                )

                if stop:
                    self._emit(
                        step_id,
                        RuntimePhase.END,
                        payload={"stop_reason": state.stop_reason},
                    )
                    break

                current_observation = observation
                state.advance_step()
                step_id += 1

                # -- after_step cancellation: break after step completes --
                if (
                    self._cancel_token.is_cancel_requested
                    and self._cancel_token.mode == CancelMode.AFTER_STEP
                ):
                    self._save_checkpoint_if_needed(
                        step_id - 1, state, task_text, task_obj
                    )
                    self._emit(
                        step_id - 1,
                        RuntimePhase.END,
                        ok=False,
                        payload={"stop_reason": "cancelled_after_step"},
                    )
                    break
        finally:
            self._teardown_env()
            self._teardown_toolsets(
                {
                    "state": state,
                    "trace_writer": self.trace_writer,
                    "task": task_obj or task_text,
                }
            )
            # Checkpoint on cancellation (immediate mode)
            if self._cancel_token.is_cancel_requested and self._checkpoint_store is not None:
                try:
                    self._save_checkpoint(step_id, state, task_text, source="cancel")
                except Exception as exc:
                    _logger.warning("Checkpoint save failed during cancellation: %s", exc)
            # Flush durability manager on run end
            if self._durability_manager is not None:
                try:
                    self._durability_manager.flush()
                except Exception as exc:
                    _logger.warning("Durability manager flush failed: %s", exc)

            # Cleanup MCP servers
            self._cleanup_mcp_servers()

        if self.trace_writer is not None:
            status = (
                "failed"
                if state.stop_reason == StopReason.UNRECOVERABLE_ERROR.value
                else "completed"
            )
            task_result = self._build_task_result(state, task_obj=task_obj, started_at=started_at)
            self.trace_writer.finalize(
                status=status,
                summary={
                    "stop_reason": state.stop_reason,
                    "final_result": state.final_result,
                    "steps": len(self.records),
                    "token_usage": self._context_runtime.tokens_total,
                    "latency_seconds": task_result.metrics.get("elapsed_seconds", 0.0),
                    "cost": task_result.metrics.get("cost", 0.0),
                    "context": self._context_runtime.run_summary(),
                    "parser": self._trace_runtime.parser_summary(),
                    "task_meta": self._task_meta(task_obj),
                    "task_result": task_result.to_dict(),
                    "run_meta": self._run_meta(),
                    "failure_report": build_failure_report(
                        self.recovery_policy, state.stop_reason
                    ),
                },
            )

        # Extract structured traces from records and events.
        _critic_traces = self._extract_critic_traces()
        _handoff_traces = self._extract_handoff_traces()

        result = EngineResult(
            state=state,
            records=self.records,
            events=self.events,
            step_count=len(self.records),
            task_result=self._build_task_result(
                state, task_obj=task_obj, started_at=started_at
            ),
            runtime_seconds=time.monotonic() - started_at,
            total_tokens=int(self._token_usage),
            run_id=self._active_run_id,
            critic_traces=_critic_traces,
            handoff_traces=_handoff_traces,
            _cancel_token=self._cancel_token,
        )
        self._notify_run_end(result)
        self._clear_active_context()
        return result

    def _apply_task_budget(self, task_obj: Optional[Task]) -> None:
        self.budget.max_steps = self._base_budget.max_steps
        self.budget.max_runtime_seconds = self._base_budget.max_runtime_seconds
        self.budget.max_tokens = self._base_budget.max_tokens
        if task_obj is not None:
            budget = task_obj.budget
            if budget.max_steps is not None:
                self.budget.max_steps = int(budget.max_steps)
            if budget.max_runtime_seconds is not None:
                self.budget.max_runtime_seconds = float(budget.max_runtime_seconds)
            if budget.max_tokens is not None:
                self.budget.max_tokens = int(budget.max_tokens)
        if self._uses_default_stop_criteria:
            self.stop_criteria = [FinalResultCriteria()]

    # -- Configuration export --------------------------------------------------

    def export_config(self) -> EngineConfig:
        """Return a serializable snapshot of this Engine's configuration."""
        return EngineConfig(
            agent_name=getattr(self.agent, "name", "") or "",
            model_id=getattr(self, "_resolved_model_id", "") or "",
            budget_max_steps=self.budget.max_steps,
            budget_max_runtime_seconds=self.budget.max_runtime_seconds,
            budget_max_tokens=self.budget.max_tokens,
            critic_names=[type(c).__name__ for c in self.critics],
            stop_criteria_names=[type(s).__name__ for s in self.stop_criteria],
            has_checkpoint_store=self._checkpoint_store is not None,
            has_tracing_provider=self._tracing_provider is not None,
            protocol_id=getattr(self, "_resolved_protocol_id", None),
            delegate_depth=self._delegate_depth,
            has_shared_memory=self._shared_memory is not None,
            has_env=self.env is not None,
            tool_count=len(self.tool_registry) if self.tool_registry else 0,
        )

    # -- Trace extraction helpers ----------------------------------------------

    def _extract_critic_traces(self) -> List[CriticTrace]:
        """Extract structured CriticTrace entries from step records."""
        traces: List[CriticTrace] = []
        for record in self.records:
            for output in list(getattr(record, "critic_outputs", []) or []):
                if not isinstance(output, dict):
                    continue
                traces.append(
                    CriticTrace(
                        step_id=record.step_id,
                        critic_name=str(output.get("critic_name", "unknown")),
                        action=str(output.get("action", "continue")),
                        reason=str(output.get("reason", "")),
                        score=float(output.get("score", 1.0)),
                        details=output.get("details", {}),
                        instruction_patch=output.get("instruction_patch"),
                        state_patch=output.get("state_patch"),
                    )
                )
        return traces

    def _extract_handoff_traces(self) -> List[HandoffTrace]:
        """Extract structured HandoffTrace entries from runtime events."""
        traces: List[HandoffTrace] = []
        for event in self.events:
            if event.phase != RuntimePhase.HANDOFF_START:
                continue
            payload = event.payload or {}
            traces.append(
                HandoffTrace(
                    step_id=event.step_id,
                    from_agent=str(payload.get("from", "")),
                    to_agent=str(payload.get("to", "")),
                    context_strategy=str(payload.get("context_strategy", "")),
                    messages_passed=int(payload.get("messages_passed", 0)),
                )
            )
        return traces

    def _build_env_view(
        self, state: StateT, step_id: int, started_at: float
    ) -> Dict[str, Any]:
        return self._env_runtime.build_env_view(state, step_id, started_at)

    def _build_initial_observation(
        self, state: StateT, step_id: int, started_at: float
    ) -> ObservationT:
        return self._env_runtime.build_initial_observation(state, step_id, started_at)

    def _build_observation_after_action(
        self,
        state: StateT,
        step_id: int,
        started_at: float,
        decision: Decision[ActionT],
        action_results: List[Any],
    ) -> ObservationT:
        return self._env_runtime.build_observation_after_action(
            state, step_id, started_at, decision, action_results
        )

    def _run_decide(
        self, state: StateT, observation: ObservationT, record: StepRecord
    ) -> Decision[ActionT]:
        # Propagate streaming callback to model runtime
        self._model_runtime.stream_callback = self.stream_callback
        try:
            return self._model_runtime.run_decide(state, observation, record)
        finally:
            self._model_runtime.stream_callback = None

    def _select_branch(
        self,
        state: StateT,
        observation: ObservationT,
        branch_decision: Decision[ActionT],
    ) -> Decision[ActionT]:
        return self._model_runtime.select_branch(state, observation, branch_decision)

    def _execute_handoff_step(
        self,
        state: StateT,
        decision: Decision[ActionT],
        record: StepRecord,
        step_id: int,
        task_text: str,
    ):
        """Execute a handoff decision: validate, swap agent, emit events.

        Returns the observation to use for the next step.
        Called from both Decision.handoff() and HandoffTool interception.
        """
        if self.agent_registry is None:
            raise ValueError("handoff requires agent_registry on Engine")
        target_name = decision.meta.get("handoff_target", "")
        # Loop detection: reject if target already in history (cycle)
        if target_name in self._handoff_history:
            from ..core.errors import QitosRuntimeError, RuntimeErrorInfo
            raise QitosRuntimeError(RuntimeErrorInfo(
                category=ErrorCategory.SYSTEM,
                message=(
                    f"Handoff loop detected: agent '{target_name}' already visited "
                    f"in this run (history: {' -> '.join(self._handoff_history)})"
                ),
                phase="handoff",
                step_id=step_id,
                recoverable=False,
            ))
        # Max handoff count guard
        max_handoffs = self.context_config.max_handoffs
        if len(self._handoff_history) >= max_handoffs:
            from ..core.errors import QitosRuntimeError, RuntimeErrorInfo
            raise QitosRuntimeError(RuntimeErrorInfo(
                category=ErrorCategory.SYSTEM,
                message=f"Maximum handoff count ({max_handoffs}) exceeded",
                phase="handoff",
                step_id=step_id,
                recoverable=False,
            ))
        current_agent_name = self.agent.name
        self._handoff_history.append(current_agent_name)
        handoff_result = self._handoff_runtime.execute_handoff(
            state, decision, record,
        )
        self._finalize_step(record, state)
        self._dispatch_hook(
            "on_after_step",
            HookContext(
                task=task_text,
                step_id=step_id,
                phase=RuntimePhase.HANDOFF_END,
                state=state,
                record=record,
            ),
        )
        # Store handoff context in state.metadata for the new agent
        state.metadata["last_handoff"] = {
            "from": handoff_result.from_agent,
            "to": handoff_result.to_agent,
        }
        # Increment handoff_count in trace metadata
        if self.trace_writer is not None:
            hc = self.trace_writer.metadata.get("handoff_count", 0) or 0
            self.trace_writer.metadata["handoff_count"] = hc + 1
        # Observation after handoff carries handoff info for reduce()
        from ..core.observation import Observation
        current_observation = Observation(
            step_id=step_id,
            metadata={
                "handoff": True,
                "from": handoff_result.from_agent,
                "to": handoff_result.to_agent,
                "context_strategy": handoff_result.context_strategy,
            },
        )
        return current_observation

    def _check_handoff_from_tool_result(
        self,
        action_results: List[Any],
    ) -> Optional[Decision[ActionT]]:
        """Check if action results signal a handoff (from HandoffTool).

        Two detection paths:
        1. ``_action_runtime`` short-circuits and returns a Decision directly
           when it sees a ``transfer_to_*`` action.
        2. HandoffTool.execute() returns a dict with ``handoff_target``
           when the tool is actually executed (fallback path).

        Returns None if no handoff is detected.
        """
        from ..core.decision import Decision as DecisionCls

        # Path 1: _action_runtime returned a Decision directly
        if isinstance(action_results, DecisionCls):
            return action_results

        # Path 2: tool was executed and returned a handoff dict
        if isinstance(action_results, list):
            for result in action_results:
                if isinstance(result, dict) and result.get("handoff_target"):
                    target = result["handoff_target"]
                    rationale = result.get("rationale", "")
                    handoff_message = result.get("message", "")
                    handoff_memory_keys = result.get("memory_keys", [])
                    meta: Dict[str, Any] = {}
                    if handoff_message:
                        meta["handoff_message"] = handoff_message
                    if handoff_memory_keys:
                        meta["handoff_memory_keys"] = handoff_memory_keys
                    return DecisionCls.handoff(
                        target=target,
                        rationale=rationale or None,
                        meta=meta if meta else None,
                        handoff_message=handoff_message or None,
                        handoff_memory_keys=handoff_memory_keys or None,
                    )
        return None

    def _run_act(
        self, state: StateT, decision: Decision[ActionT], record: StepRecord
    ) -> List[Any]:
        return self._action_runtime.run_act(state, decision, record)

    def _run_reduce(
        self,
        state: StateT,
        observation: ObservationT,
        decision: Decision[ActionT],
        record: StepRecord,
    ) -> None:
        self._control_runtime.run_reduce(state, observation, decision, record)

    def _apply_critics(self, state: StateT, record: StepRecord) -> Any:
        return self._control_runtime.apply_critics(state, record)

    def _apply_critic_patches(self, state: StateT, critic_result: Dict[str, Any]) -> None:
        """Apply modified_prompt, instruction_patch, and state_patch from critic retry."""
        # Store patches so they can be picked up by the next decide() call
        modified_prompt = critic_result.get("modified_prompt")
        instruction_patch = critic_result.get("instruction_patch")
        state_patch = critic_result.get("state_patch")

        if modified_prompt is not None:
            self._critic_modified_prompt = modified_prompt
        if instruction_patch is not None:
            self._critic_instruction_patch = instruction_patch
        if state_patch is not None:
            for key, value in state_patch.items():
                if hasattr(state, key):
                    setattr(state, key, value)

    def _run_check_stop(
        self,
        state: StateT,
        decision: Decision[ActionT],
        step_id: int,
        started_at: float,
    ) -> bool:
        return self._control_runtime.run_check_stop(
            state, decision, step_id, started_at
        )

    def _finish_check_stop(
        self,
        step_id: int,
        state: StateT,
        decision: Decision[ActionT],
        stop: bool,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._control_runtime._finish_check_stop(
            step_id, state, decision, stop, extra_payload
        )

    def _should_stop_by_criteria(
        self, state: StateT, step_id: int, elapsed_seconds: float
    ) -> tuple[bool, Optional[StopReason], Optional[str]]:
        return self._control_runtime.should_stop_by_criteria(
            state, step_id, elapsed_seconds
        )

    def _budget_exhausted(self, step_id: int, started_at: float, state: StateT) -> bool:
        return self._control_runtime.budget_exhausted(step_id, started_at, state)

    def _normalize_decision(self, raw_decision: Any, step: int) -> Decision[ActionT]:
        return self._model_runtime.normalize_decision(raw_decision, step)

    def _compute_state_diff(
        self, before: Dict[str, Any], after: Dict[str, Any]
    ) -> Dict[str, Any]:
        diff: Dict[str, Any] = {}
        all_keys = set(before.keys()) | set(after.keys())
        for key in all_keys:
            b = before.get(key)
            a = after.get(key)
            if b != a:
                diff[key] = {"before": b, "after": a}
        return diff

    def _recover(self, state: StateT, phase: RuntimePhase, exc: Exception) -> bool:
        return self._control_runtime.recover(state, phase, exc)

    def _emit(
        self,
        step_id: int,
        phase: RuntimePhase,
        ok: bool = True,
        payload: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        self._trace_runtime.emit(step_id, phase, ok=ok, payload=payload, error=error)

    def _write_trace_event(self, event: RuntimeEvent) -> None:
        self._trace_runtime.write_trace_event(event)

    def _write_trace_step(self, step: StepRecord) -> None:
        self._trace_runtime.write_trace_step(step)

    def _finalize_step(self, record: StepRecord, state: StateT) -> None:
        self._trace_runtime.finalize_step(record, state)

    def _save_checkpoint_if_needed(
        self, step_id: int, state: StateT, task_text: str, task_obj: Optional[Any]
    ) -> None:
        # --- New CheckpointStore path ---
        if self._checkpoint_store is not None:
            self._save_checkpoint(step_id, state, task_text, source="loop")
            return
        # --- Legacy CheckpointManager path (deprecated) ---
        if self.checkpoint_manager is None:
            return
        if not self.checkpoint_manager.should_checkpoint(step_id):
            return
        from ..checkpoint import CheckpointData

        task_dict = None
        if task_obj is not None and hasattr(task_obj, "to_dict"):
            task_dict = task_obj.to_dict()
        checkpoint = CheckpointData(
            run_id=self._active_run_id,
            step_id=step_id,
            state_dict=state.to_dict(),
            step_records=[asdict(r) for r in self.records],
            runtime_events=[asdict(e) for e in self.events],
            budget=asdict(self.budget),
            token_usage=int(self._token_usage),
            task_text=task_text,
            task_dict=task_dict,
        )
        try:
            self.checkpoint_manager.save(checkpoint)
        except OSError:
            pass

    def _save_checkpoint(
        self,
        step_id: int,
        state: StateT,
        task_text: str,
        source: str = "loop",
    ) -> None:
        """Save a checkpoint via the new CheckpointStore (every step)."""
        if self._checkpoint_store is None or self._durability_manager is None:
            return

        state_data = state.to_dict()
        new_versions: StateVersions = {}
        if self._version_tracker is not None:
            # Compute diff from last snapshot and bump versions
            new_versions = self._version_tracker.snapshot()

        checkpoint = Checkpoint(
            id=CheckpointId(uuid4().hex),
            thread_id=self._active_run_id,
            step=step_id,
            state_data=state_data,
            state_versions=new_versions,
            versions_seen={},
            pending_writes=[],
            parent_id=self._last_checkpoint_id,
        )

        metadata: CheckpointMetadata = {
            "source": source,
            "step": step_id,
            "run_id": self._active_run_id,
        }

        config = CheckpointConfig(thread_id=self._active_run_id)
        self._durability_manager.put(config, checkpoint, metadata, new_versions)
        self._last_checkpoint_id = checkpoint.id

        # Also flush pending writes if any
        if self._pending_write_manager is not None:
            write_config = CheckpointConfig(
                thread_id=self._active_run_id,
                checkpoint_id=checkpoint.id,
            )
            self._pending_write_manager.commit_writes(write_config)

    def resume_from_checkpoint(
        self,
        config: CheckpointConfig,
    ) -> EngineResult:
        """Resume a run from a saved checkpoint.

        Args:
            config: CheckpointConfig pointing to the checkpoint to resume from.

        Returns:
            EngineResult from the resumed run.
        """
        if self._checkpoint_store is None:
            raise RuntimeError("No checkpoint_store configured; cannot resume.")

        tuple_ = self._checkpoint_store.get_tuple(config)
        if tuple_ is None:
            raise ValueError(f"Checkpoint not found: {config}")

        checkpoint = tuple_.checkpoint
        state_cls = cast(Type[StateSchema], type(self._active_state) if self._active_state is not None else StateSchema)
        state = state_cls.from_dict(checkpoint.state_data)

        # Restore version tracker
        if self._version_tracker is not None:
            self._version_tracker.apply_versions(checkpoint.state_versions)

        # Set up internal state for resume
        self._active_run_id = checkpoint.thread_id
        self._last_checkpoint_id = checkpoint.id
        self._active_task = tuple_.metadata.get("run_id", "")
        self._active_state = state  # type: ignore[assignment]

        # Load pending writes for crash recovery
        if self._pending_write_manager is not None:
            resume_config = CheckpointConfig(
                thread_id=checkpoint.thread_id,
                checkpoint_id=checkpoint.id,
            )
            self._pending_write_manager.load_pending_from_store(resume_config)

        # Continue the run from the next step
        return self.run(
            self._active_task,
            _resume_state=state,
            _resume_step=checkpoint.step + 1,
        )

    def resume(
        self,
        checkpoint_id: CheckpointId,
        resume_value: Any = None,
        resume_values: Optional[Dict[str, Any]] = None,
    ) -> EngineResult:
        """Resume an interrupted run.

        Args:
            checkpoint_id: The checkpoint to resume from.
            resume_value: Value to pass to the first ``interrupt()`` call.
            resume_values: Dict mapping interrupt IDs to values for
                multiple interrupts.

        Returns:
            EngineResult from the resumed run.
        """
        from .interrupt import _set_resume_values

        # Prepare resume values
        values: Dict[str, Any] = dict(resume_values or {})
        if resume_value is not None and not values:
            # Default: map to the first interrupt
            values["int_1"] = resume_value

        _set_resume_values(values)

        config = CheckpointConfig(
            thread_id=self._active_run_id,
            checkpoint_id=checkpoint_id,
        )
        result = self.resume_from_checkpoint(config)
        return result

    def _save_interrupt_checkpoint(
        self,
        step_id: int,
        state: StateT,
        interrupt_exc: Any,
    ) -> CheckpointId:
        """Save a checkpoint when an interrupt fires.  Returns the checkpoint ID."""
        from .interrupt import EngineInterrupt

        if self._checkpoint_store is None:
            # No store configured — generate a transient ID
            return CheckpointId(uuid4().hex)

        self._save_checkpoint(step_id, state, self._active_task, source="interrupt")
        # Update the interrupt exception with the checkpoint ID
        if isinstance(interrupt_exc, EngineInterrupt) and self._last_checkpoint_id is not None:
            interrupt_exc.checkpoint_id = self._last_checkpoint_id
            return self._last_checkpoint_id
        return CheckpointId(uuid4().hex)

    def _memory_append(
        self,
        role: str,
        content: Any,
        step_id: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        memory = self._memory()
        if memory is None:
            return
        memory.append(
            MemoryRecord(
                role=role, content=content, step_id=step_id, metadata=metadata or {}
            )
        )

    def _memory(self) -> Optional[Memory]:
        mem = getattr(self.agent, "memory", None)
        return mem if isinstance(mem, Memory) else None

    def _history(self) -> History:
        hist = getattr(self.agent, "history", None)
        if isinstance(hist, History):
            return hist
        if getattr(self.agent, "llm", None) is not None and self.context_config.enabled:
            try:
                from ..kit.history import CompactHistory

                if not isinstance(self._runtime_history, CompactHistory):
                    self._runtime_history = CompactHistory(
                        llm=getattr(self.agent, "llm", None),
                        max_tokens=max(
                            1024,
                            int(
                                (
                                    self._context_runtime.resolve_request_budget(
                                        getattr(self.agent, "llm", None)
                                    ).get("available_input_budget")
                                    or 16000
                                )
                            ),
                        ),
                    )
            except Exception as exc:
                _logger.debug("Failed to append context telemetry to history: %s", exc)
        return self._runtime_history

    def _history_append(
        self,
        role: str,
        content: Any,
        step_id: int,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        history = self._history()
        history.append(
            HistoryMessage(
                role=role,
                content=content,
                step_id=step_id,
                metadata=metadata or {},
                tool_calls=[dict(x) for x in list(tool_calls or []) if isinstance(x, dict)],
                tool_call_id=tool_call_id,
                name=name,
            )
        )

    def _normalize_history_messages(self, payload: Any) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if not isinstance(payload, list):
            return messages
        for item in payload:
            if isinstance(item, HistoryMessage):
                role = str(item.role).strip()
                if not role:
                    continue
                message: Dict[str, Any] = {"role": role, "content": item.content}
                message["_step_id"] = int(item.step_id)
                if item.tool_calls:
                    message["tool_calls"] = [dict(x) for x in item.tool_calls]
                if item.tool_call_id:
                    message["tool_call_id"] = str(item.tool_call_id)
                if item.name:
                    message["name"] = str(item.name)

                if role not in {"assistant", "tool"}:
                    content = str(item.content or "")
                    if not content:
                        continue
                    message["content"] = content
                elif (
                    message.get("content") in (None, "")
                    and not message.get("tool_calls")
                    and not message.get("tool_call_id")
                ):
                    continue
                messages.append(message)
                continue
            if isinstance(item, dict):
                role = str(item.get("role", "")).strip()
                if not role:
                    continue
                payload_message: Dict[str, Any] = {
                    "role": role,
                    "content": item.get("content"),
                }
                step_value = item.get("step_id")
                if step_value is not None:
                    try:
                        payload_message["_step_id"] = int(step_value)
                    except Exception as exc:
                        _logger.debug("Failed to parse step_id: %s", exc)
                tool_calls = item.get("tool_calls")
                if isinstance(tool_calls, list):
                    payload_message["tool_calls"] = [
                        dict(x) for x in tool_calls if isinstance(x, dict)
                    ]
                if item.get("tool_call_id") not in (None, ""):
                    payload_message["tool_call_id"] = str(item.get("tool_call_id"))
                if item.get("name") not in (None, ""):
                    payload_message["name"] = str(item.get("name"))

                if role not in {"assistant", "tool"}:
                    content = str(payload_message.get("content") or "")
                    if not content:
                        continue
                    payload_message["content"] = content
                elif (
                    payload_message.get("content") in (None, "")
                    and not payload_message.get("tool_calls")
                    and not payload_message.get("tool_call_id")
                ):
                    continue
                messages.append(payload_message)
        return messages

    def _hook_context(self, **kwargs: Any) -> HookContext:
        return HookContext(task=self._active_task, **kwargs)

    def _infer_failed_phase(self, record: StepRecord) -> RuntimePhase:
        return self._control_runtime.infer_failed_phase(record)

    def _normalize_task(self, task: str | Task) -> tuple[Optional[Task], str]:
        if isinstance(task, Task):
            return task, task.objective
        return None, str(task)

    def _preflight_validate(
        self, task_obj: Optional[Task], workspace: Any = None
    ) -> List[TaskValidationIssue]:
        issues: List[TaskValidationIssue] = []
        if task_obj is not None:
            try:
                issues.extend(
                    task_obj.validate_structured(
                        workspace=str(workspace) if workspace else None
                    )
                )
            except Exception as exc:
                issues.append(
                    TaskValidationIssue(
                        code="TASK_VALIDATION_EXCEPTION",
                        message=str(exc),
                        field="task",
                    )
                )

        for issue in self._validate_env_capabilities():
            issues.append(
                TaskValidationIssue(
                    code=str(issue.get("code", "ENV_CAPABILITY_ERROR")),
                    message=str(
                        issue.get("message", "Environment capability mismatch")
                    ),
                    field=str(issue.get("field", "env")),
                    details=(
                        issue.get("details", {})
                        if isinstance(issue.get("details", {}), dict)
                        else {}
                    ),
                )
            )
        health = self._validate_env_health()
        if health is not None:
            issues.append(
                TaskValidationIssue(
                    code=str(health.get("code", "ENV_HEALTH_CHECK_FAILED")),
                    message=str(
                        health.get("message", "Environment health check failed")
                    ),
                    field=str(health.get("field", "env")),
                    details=(
                        health.get("details", {})
                        if isinstance(health.get("details", {}), dict)
                        else {}
                    ),
                )
            )
        return issues

    def _validate_env_capabilities(self) -> List[Dict[str, Any]]:
        return self._env_runtime.validate_env_capabilities()

    def _collect_required_ops(self) -> set[str]:
        return self._env_runtime.collect_required_ops()

    def _validate_env_health(self) -> Optional[Dict[str, Any]]:
        return self._env_runtime.validate_env_health()

    def _setup_env(
        self, task_obj: Optional[Task], state: StateT, kwargs: Dict[str, Any]
    ) -> None:
        self._env_runtime.setup_env(task_obj, state, kwargs)

    def _build_env_from_spec(
        self, env_spec: Any, fallback_workspace: Any = None
    ) -> Optional[Env]:
        return self._env_runtime.build_env_from_spec(env_spec, fallback_workspace)

    def _teardown_env(self) -> None:
        self._env_runtime.teardown_env()

    def _run_env_step(
        self, decision: Decision[ActionT], action_results: List[Any]
    ) -> Optional[EnvStepResult]:
        return self._env_runtime.run_env_step(decision, action_results)

    def _env_payload(self) -> Dict[str, Any]:
        return self._env_runtime.env_payload()

    def _env_identity(self) -> Dict[str, Any]:
        return self._env_runtime.env_identity()

    def _env_observation_to_dict(
        self, observation: Optional[EnvObservation]
    ) -> Optional[Dict[str, Any]]:
        return self._env_runtime.env_observation_to_dict(observation)

    def _env_step_result_to_dict(
        self, result: Optional[EnvStepResult]
    ) -> Optional[Dict[str, Any]]:
        return self._env_runtime.env_step_result_to_dict(result)

    def _setup_toolsets(self, context: Dict[str, Any]) -> None:
        if not hasattr(self.tool_registry, "setup"):
            return
        self._write_lifecycle_event("toolset_setup_start", context)
        try:
            self.tool_registry.setup(context)
            self._write_lifecycle_event("toolset_setup_end", context)
        except Exception as exc:
            self._write_lifecycle_event(
                "toolset_setup_error", context, ok=False, error=str(exc)
            )

    def _teardown_toolsets(self, context: Dict[str, Any]) -> None:
        if not hasattr(self.tool_registry, "teardown"):
            return
        self._write_lifecycle_event("toolset_teardown_start", context)
        try:
            self.tool_registry.teardown(context)
            self._write_lifecycle_event("toolset_teardown_end", context)
        except Exception as exc:
            self._write_lifecycle_event(
                "toolset_teardown_error", context, ok=False, error=str(exc)
            )

    def _write_lifecycle_event(
        self,
        phase: str,
        payload: Dict[str, Any],
        ok: bool = True,
        error: Optional[str] = None,
    ) -> None:
        self._trace_runtime.write_lifecycle_event(phase, payload, ok=ok, error=error)

    def _estimate_tokens(self, payload: Any) -> int:
        text = payload if isinstance(payload, str) else repr(payload)
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _task_meta(self, task_obj: Optional[Task]) -> Optional[Dict[str, Any]]:
        return self._trace_runtime.task_meta(task_obj)

    def _task_issue_to_dict(self, issue: TaskValidationIssue) -> Dict[str, Any]:
        return self._trace_runtime.task_issue_to_dict(issue)

    def _hydrate_trace_metadata(self, task_obj: Optional[Task], task_text: str) -> None:
        self._trace_runtime.hydrate_trace_metadata(task_obj, task_text)

    def _run_meta(self) -> Dict[str, Any]:
        return self._trace_runtime.run_meta()

    def _build_task_result(
        self, state: StateT, task_obj: Optional[Task], started_at: float
    ) -> TaskResult:
        return self._trace_runtime.build_task_result(state, task_obj, started_at)

    def _sanitize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._trace_runtime.sanitize_payload(payload)

    def _notify_event(self, event: RuntimeEvent, state: StateT) -> None:
        self._trace_runtime.notify_event(event, state)

    def _notify_run_start(self, task: str, state: StateT) -> None:
        self._trace_runtime.notify_run_start(task, state)

    def _notify_run_end(self, result: EngineResult[StateT]) -> None:
        self._trace_runtime.notify_run_end(result)

    def _dispatch_hook(self, method_name: str, ctx: HookContext) -> None:
        self._trace_runtime.dispatch_hook(method_name, ctx)

    def _inject_hook_payload(self, method_name: str, ctx: HookContext) -> None:
        self._trace_runtime.inject_hook_payload(method_name, ctx)

    def _reset_run_state(self) -> None:
        self._trace_runtime.reset_run_state()
        self._resolved_protocol = None
        self._resolved_protocol_source = ""
        self._last_prompt_metadata = {}
        self._tool_loop_detector.reset()
        self._handoff_history = []
        self._critic_modified_prompt = None
        self._critic_instruction_patch = None
        self._cancel_token.clear()

    # -- MCP server lifecycle helpers --

    def _connect_mcp_servers(self) -> None:
        """Connect all configured MCP servers and bridge their tools."""
        from ..mcp.bridge import mcp_server_to_function_tools

        for server in self.agent.mcp_servers:
            try:
                if hasattr(server, "connect"):
                    server.connect()
                self._connected_mcp_servers.append(server)
                # Bridge MCP tools into the engine's tool registry
                if self.tool_registry is not None:
                    tools = asyncio.run(mcp_server_to_function_tools(server))
                    for tool in tools:
                        if hasattr(self.tool_registry, "register"):
                            self.tool_registry.register(tool)
            except Exception as exc:
                # Log but don't fail the entire run for one bad MCP server
                _logger.debug("MCP server connection failed: %s", exc)

    def _cleanup_mcp_servers(self) -> None:
        """Cleanup all connected MCP servers."""
        for server in self._connected_mcp_servers:
            try:
                if hasattr(server, "cleanup"):
                    server.cleanup()
            except Exception as exc:
                _logger.debug("MCP server cleanup failed: %s", exc)
        self._connected_mcp_servers = []

    # -- Handoff tool helpers --

    def _register_handoff_tools(self) -> None:
        """Register HandoffTool for each declared handoff target."""
        from ..kit.tool.handoff_tool import HandoffTool

        targets = self.agent.handoff_targets or []
        for target_name in targets:
            # Resolve description from agent registry if available
            description = ""
            if self.agent_registry is not None:
                try:
                    spec = self.agent_registry.resolve(target_name)
                    description = getattr(spec, "description", "") or ""
                except Exception as exc:
                    _logger.debug("Failed to resolve handoff target %s: %s", target_name, exc)

            tool = HandoffTool(
                target_name=target_name,
                target_description=description,
            )
            if hasattr(self.tool_registry, "register"):
                self.tool_registry.register(tool)
            self._handoff_tools.append(tool)

    def _intercept_handoff_action(self, action: Any) -> Any | None:
        """Check if an action is a handoff tool call. Return Decision.handoff() or None."""
        if not action.name.startswith("transfer_to_"):
            return None

        from ..core.decision import Decision

        target = action.name.replace("transfer_to_", "", 1)
        rationale = ""
        handoff_message = ""
        handoff_memory_keys: List[str] = []
        if isinstance(action.args, dict):
            rationale = action.args.get("rationale", "")
            handoff_message = action.args.get("message", "")
            handoff_memory_keys = action.args.get("memory_keys", [])

        return Decision.handoff(
            target=target,
            rationale=rationale or None,
            handoff_message=handoff_message or None,
            handoff_memory_keys=handoff_memory_keys or None,
        )

    def _harness_mismatch_diagnostics(self) -> Dict[str, Any]:
        llm = getattr(self.agent, "llm", None)
        metadata = dict(getattr(llm, "qitos_harness_metadata", {}) or {})
        expected_protocol = str(metadata.get("protocol") or "").strip()
        expected_parser = str(metadata.get("parser") or "").strip()
        active_protocol = getattr(self.resolve_protocol(), "id", None)
        parser = self.parser or getattr(self.agent, "model_parser", None)
        active_parser = parser.__class__.__name__ if parser is not None else None
        mismatch_fields: List[str] = []
        if expected_protocol and active_protocol and expected_protocol != active_protocol:
            mismatch_fields.append("protocol")
        if expected_parser and active_parser and expected_parser != active_parser:
            mismatch_fields.append("parser")
        return {
            "mismatch": bool(mismatch_fields),
            "mismatch_fields": mismatch_fields,
            "expected_protocol": expected_protocol or None,
            "active_protocol": active_protocol,
            "expected_parser": expected_parser or None,
            "active_parser": active_parser,
            "model_name": getattr(llm, "model", None)
            or getattr(llm, "model_name", None),
        }

    def _clear_active_context(self) -> None:
        self._trace_runtime.clear_active_context()


__all__ = ["Engine", "EngineResult"]
