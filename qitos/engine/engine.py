"""Canonical Engine for AgentModule execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar
from uuid import uuid4

from ..core.agent_module import AgentModule
from ..core.decision import Decision
from ..core.errors import ErrorCategory, StopReason
from ..core.env import Env, EnvObservation, EnvStepResult
from ..core.history import History, HistoryMessage, HistoryPolicy
from ..core.memory import Memory, MemoryRecord
from ..core.state import StateSchema
from ..core.task import Task, TaskResult, TaskValidationIssue
from ..trace import TraceWriter
from ..protocols import get_protocol, infer_protocol_from_parser
from ..models.profile_registry import infer_default_protocol, infer_model_profile
from ._action_runtime import _ActionRuntime
from ._context_runtime import _ContextRuntime
from ._control_runtime import _ControlRuntime
from ._env_runtime import _EnvRuntime
from ._model_runtime import _ModelRuntime
from ._trace_runtime import _TraceRuntime
from .action_executor import ActionExecutor
from .branching import BranchSelector, FirstCandidateSelector
from .critic import Critic
from .hooks import EngineHook, HookContext
from .parser import Parser
from .recovery import RecoveryPolicy, build_failure_report
from .search import Search
from .states import ContextConfig, RuntimeBudget, RuntimeEvent, RuntimePhase, StepRecord
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
            f"[{x.step_id}] {x.role}: {x.content[:120]}"
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
class EngineResult(Generic[StateT]):
    state: StateT
    records: List[StepRecord]
    events: List[RuntimeEvent]
    step_count: int
    task_result: Optional[TaskResult] = None


class Engine(Generic[StateT, ObservationT, ActionT]):
    """Single execution kernel for all AgentModule workflows."""

    def __init__(
        self,
        agent: AgentModule[StateT, ObservationT, ActionT],
        budget: Optional[RuntimeBudget] = None,
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
    ):
        self.agent = agent
        self.tool_registry = agent.tool_registry
        self.budget = budget or RuntimeBudget(max_steps=10)
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

        self.executor = (
            ActionExecutor(tool_registry=self.tool_registry)
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
        self._last_system_prompt: str = ""
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
        self._context_runtime = _ContextRuntime(self)
        self._context_runtime.apply_config(self.context_config)

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

    def run(self, task: str | Task, **kwargs: Any) -> EngineResult[StateT]:
        self._reset_run_state()
        memory = self._memory()
        if memory is not None:
            try:
                memory.reset()
            except Exception:
                pass
        try:
            self._history().reset()
        except Exception:
            pass
        if hasattr(self.recovery_policy, "reset"):
            try:
                self.recovery_policy.reset()
            except Exception:
                pass
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
        self._emit(
            0,
            RuntimePhase.INIT,
            payload={
                "task": task_text,
                "task_id": task_obj.id if task_obj is not None else None,
                "task_meta": self._task_meta(task_obj),
                "run_meta": self._run_meta(),
                "env": self._env_identity(),
            },
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

        step_id = 0
        current_observation = self._build_initial_observation(
            state, step_id, started_at
        )
        try:
            while True:
                if self._budget_exhausted(step_id, started_at, state):
                    self._emit(
                        step_id,
                        RuntimePhase.END,
                        ok=False,
                        payload={"stop_reason": state.stop_reason},
                    )
                    break

                self.validation_gate.before_phase(state, RuntimePhase.DECIDE.value)

                record = StepRecord(step_id=step_id)
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
                    action_results = self._run_act(state, decision, record)
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

                critic_action = self._apply_critics(state, record)
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
        finally:
            self._teardown_env()
            self._teardown_toolsets(
                {
                    "state": state,
                    "trace_writer": self.trace_writer,
                    "task": task_obj or task_text,
                }
            )

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

        result = EngineResult(
            state=state,
            records=self.records,
            events=self.events,
            step_count=len(self.records),
            task_result=self._build_task_result(
                state, task_obj=task_obj, started_at=started_at
            ),
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
        return self._model_runtime.run_decide(state, observation, record)

    def _select_branch(
        self,
        state: StateT,
        observation: ObservationT,
        branch_decision: Decision[ActionT],
    ) -> Decision[ActionT]:
        return self._model_runtime.select_branch(state, observation, branch_decision)

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

    def _apply_critics(self, state: StateT, record: StepRecord) -> str:
        return self._control_runtime.apply_critics(state, record)

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
            except Exception:
                pass
        return self._runtime_history

    def _history_append(
        self,
        role: str,
        content: str,
        step_id: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        history = self._history()
        history.append(
            HistoryMessage(
                role=role, content=content, step_id=step_id, metadata=metadata or {}
            )
        )

    def _normalize_history_messages(self, payload: Any) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if not isinstance(payload, list):
            return messages
        for item in payload:
            if isinstance(item, HistoryMessage):
                role = str(item.role).strip()
                content = str(item.content)
                if role and content:
                    messages.append({"role": role, "content": content})
                continue
            if isinstance(item, dict):
                role = str(item.get("role", "")).strip()
                content = str(item.get("content", ""))
                if role and content:
                    messages.append({"role": role, "content": content})
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

    def _clear_active_context(self) -> None:
        self._trace_runtime.clear_active_context()


__all__ = ["Engine", "EngineResult"]
