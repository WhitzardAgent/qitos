"""Private trace/hook helpers for Engine."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generic, Optional, TypeVar

from ..core.errors import StopReason
from ..core.spec import ExperimentSpec, RunSpec
from ..core.state import StateSchema
from ..core.task import Task, TaskCriterionResult, TaskResult, TaskValidationIssue
from ..trace import runtime_event_to_trace, runtime_step_to_trace
from ._protocol import _EngineProtocol
from .hooks import HookContext
from .states import RuntimeEvent, RuntimePhase, StepRecord

_logger = logging.getLogger("qitos.engine._trace_runtime")


StateT = TypeVar("StateT", bound=StateSchema)


class _TraceRuntime(Generic[StateT]):
    def __init__(self, engine: _EngineProtocol):
        self.engine = engine
        self.parser_error_count = 0
        self.parser_warning_count = 0
        self.parser_salvage_count = 0
        self.parser_error_codes: Counter[str] = Counter()

    def emit(
        self,
        step_id: int,
        phase: RuntimePhase,
        ok: bool = True,
        payload: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        engine = self.engine
        event_ts = datetime.now(timezone.utc).isoformat()
        event_payload = dict(payload or {})
        event_payload.setdefault("run_id", engine._active_run_id)
        event_payload.setdefault("step_id", step_id)
        event_payload.setdefault("phase", phase.value)
        event_payload.setdefault("ts", event_ts)
        event = RuntimeEvent(
            step_id=step_id,
            phase=phase,
            ok=ok,
            payload=event_payload,
            error=error,
            ts=event_ts,
        )
        engine.events.append(event)
        if engine.records and engine.records[-1].step_id == step_id:
            engine.records[-1].phase_events.append(event)
        self.write_trace_event(event)
        state = engine._active_state
        if state is not None:
            self.notify_event(event, state)

    def write_trace_event(self, event: RuntimeEvent) -> None:
        # Route through TracingProvider if available
        provider = getattr(self.engine, "_tracing_provider", None)
        if provider is not None:
            # The LegacyTraceWriterProcessor handles the bridge,
            # so we don't need to also write directly.
            return
        if self.engine.trace_writer is None:
            return
        self.engine.trace_writer.write_event(
            runtime_event_to_trace(self.engine.trace_writer.run_id, event)
        )

    def write_trace_step(self, step: StepRecord) -> None:
        # Route through TracingProvider if available
        provider = getattr(self.engine, "_tracing_provider", None)
        if provider is not None:
            return
        if self.engine.trace_writer is None:
            return
        self.engine.trace_writer.write_step(runtime_step_to_trace(step))

    def finalize_step(self, record: StepRecord, state: StateT) -> None:
        self.write_trace_step(record)
        for hook in self.engine.hooks:
            on_step_end = getattr(hook, "on_step_end", None)
            if on_step_end is None:
                continue
            try:
                on_step_end(record=record, state=state, engine=self.engine)
            except Exception as exc:
                _logger.debug("on_step_end hook raised: %s", exc)
                continue

    def write_lifecycle_event(
        self,
        phase: str,
        payload: Dict[str, Any],
        ok: bool = True,
        error: Optional[str] = None,
    ) -> None:
        if self.engine.trace_writer is None:
            return
        from ..trace.events import TraceEvent

        event = TraceEvent(
            run_id=self.engine.trace_writer.run_id,
            step_id=0,
            phase=phase,
            payload=self.sanitize_payload(payload),
            ok=ok,
            error=error,
        )
        self.engine.trace_writer.write_event(event)

    def task_meta(self, task_obj: Optional[Task]) -> Optional[Dict[str, Any]]:
        if task_obj is None:
            return None
        payload = task_obj.to_dict()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {
            "task_id": task_obj.id,
            "env_spec": payload.get("env_spec"),
            "budget": payload.get("budget"),
            "success_criteria": payload.get("success_criteria", []),
            "input_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16],
        }

    def task_issue_to_dict(self, issue: TaskValidationIssue) -> Dict[str, Any]:
        return {
            "code": issue.code,
            "message": issue.message,
            "field": issue.field,
            "details": issue.details,
        }

    def hydrate_trace_metadata(self, task_obj: Optional[Task], task_text: str) -> None:
        engine = self.engine
        if engine.trace_writer is None:
            return
        run_meta = self.run_meta()
        task_meta = self.task_meta(task_obj) or {}
        prompt_seed = {
            "task": task_text,
            "agent": getattr(engine.agent, "name", engine.agent.__class__.__name__),
            "parser": run_meta.get("parser"),
            "model_name": run_meta.get("model_name"),
            "tool_count": run_meta.get("tool_count"),
            "protocol": run_meta.get("protocol"),
        }
        prompt_hash = hashlib.sha256(
            json.dumps(prompt_seed, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        run_cfg_hash = hashlib.sha256(
            json.dumps(
                {"task_meta": task_meta, "run_meta": run_meta}, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()[:16]
        engine.trace_writer.metadata.update(
            {
                "model_id": run_meta.get("model_name") or "unknown",
                "prompt_hash": prompt_hash,
                "tool_versions": {
                    item.get("name", ""): item.get("origin", {})
                    for item in run_meta.get("tools", [])
                    if isinstance(item, dict)
                },
                "seed": (
                    getattr(engine.agent, "config", {}).get("seed")
                    if isinstance(getattr(engine.agent, "config", {}), dict)
                    else None
                ),
                "run_config_hash": run_cfg_hash,
                "task_hash": task_meta.get("input_hash"),
                "env_fingerprint": run_meta.get("env"),
                "prompt_builder": (engine._last_prompt_metadata or {}).get(
                    "prompt_builder"
                ),
                "inference_task_id": run_meta.get("inference_task_id"),
            }
        )
        run_spec = getattr(engine, "run_spec", None)
        if isinstance(run_spec, RunSpec):
            if not run_spec.model_name and run_meta.get("model_name"):
                run_spec.model_name = str(run_meta.get("model_name"))
            if not run_spec.model_family and run_spec.model_name:
                inferred = RunSpec.infer(model_name=run_spec.model_name)
                run_spec.model_family = inferred.model_family
            if not run_spec.prompt_protocol and run_meta.get("protocol"):
                run_spec.prompt_protocol = str(run_meta.get("protocol"))
            if not run_spec.parser_name and run_meta.get("parser"):
                run_spec.parser_name = str(run_meta.get("parser"))
            if not run_spec.tool_manifest and isinstance(run_meta.get("tools"), list):
                run_spec.tool_manifest = list(run_meta.get("tools") or [])
            if not run_spec.toolset_name and engine.tool_registry is not None:
                run_spec.toolset_name = engine.tool_registry.__class__.__name__
            if not run_spec.environment and isinstance(run_meta.get("env"), dict):
                run_spec.environment = dict(run_meta.get("env") or {})
            if task_obj is not None:
                benchmark_name = (
                    task_obj.metadata.get("benchmark")
                    or task_obj.inputs.get("benchmark")
                    or task_obj.metadata.get("benchmark_name")
                )
                benchmark_split = (
                    task_obj.metadata.get("split")
                    or task_obj.inputs.get("split")
                    or task_obj.metadata.get("benchmark_split")
                )
                if not run_spec.benchmark_name and benchmark_name:
                    run_spec.benchmark_name = str(benchmark_name)
                if not run_spec.benchmark_split and benchmark_split:
                    run_spec.benchmark_split = str(benchmark_split)
            engine.trace_writer.metadata.update(
                {
                    "git_sha": run_spec.git_sha,
                    "package_version": run_spec.package_version,
                    "benchmark_name": run_spec.benchmark_name,
                    "benchmark_split": run_spec.benchmark_split,
                    "model_family": run_spec.model_family,
                    "prompt_protocol": run_spec.prompt_protocol,
                    "parser_name": run_spec.parser_name,
                    "tool_manifest": list(run_spec.tool_manifest or []),
                    "run_spec": run_spec.to_dict(),
                    "official_run": run_spec.is_official_run(),
                }
            )
        experiment_spec = getattr(engine, "experiment_spec", None)
        if isinstance(experiment_spec, ExperimentSpec):
            engine.trace_writer.metadata["experiment_spec"] = experiment_spec.to_dict()

    def run_meta(self) -> Dict[str, Any]:
        engine = self.engine
        llm = getattr(engine.agent, "llm", None)
        model_name = getattr(llm, "model", None) if llm is not None else None
        harness_meta = (
            dict(getattr(llm, "qitos_harness_metadata", {}) or {})
            if llm is not None
            else {}
        )
        inference_task_id = ""
        if llm is not None:
            inference_task_id = str(
                getattr(llm, "inference_key", None)
                or harness_meta.get("inference_task_id")
                or harness_meta.get("inference_key")
                or ""
            ).strip()
        protocol = engine.resolve_protocol() if hasattr(engine, "resolve_protocol") else None
        parser_name = (
            engine.parser.__class__.__name__
            if engine.parser is not None
            else (
                engine.agent.model_parser.__class__.__name__
                if getattr(engine.agent, "model_parser", None) is not None
                else None
            )
        )
        tools = []
        if engine.tool_registry is not None and hasattr(
            engine.tool_registry, "list_tools"
        ):
            try:
                for name in engine.tool_registry.list_tools():
                    if hasattr(engine.tool_registry, "describe_tool"):
                        tools.append(engine.tool_registry.describe_tool(name))
                    else:
                        tools.append({"name": name})
            except Exception as exc:
                _logger.debug("Failed to list tools: %s", exc)
        env_info = engine._env_identity()
        return {
            "model_name": model_name,
            "model_family": (
                harness_meta.get("family_preset")
                or (RunSpec.infer(model_name=model_name).model_family if model_name else None)
            ),
            "protocol": getattr(protocol, "id", None) if protocol is not None else None,
            "protocol_resolution_source": getattr(
                engine, "_resolved_protocol_source", ""
            ),
            "parser": parser_name,
            "tool_count": len(tools),
            "tools": tools,
            "env": env_info,
            "context": engine._context_runtime.run_meta(llm),
            "prompt": dict(getattr(engine, "_last_prompt_metadata", {}) or {}),
            "harness": harness_meta,
            "inference_task_id": inference_task_id or None,
            "run_spec": (
                engine.run_spec.to_dict()
                if isinstance(getattr(engine, "run_spec", None), RunSpec)
                else None
            ),
            "experiment_spec": (
                engine.experiment_spec.to_dict()
                if isinstance(getattr(engine, "experiment_spec", None), ExperimentSpec)
                else None
            ),
        }

    def record_parser_diagnostics(self, diagnostics: Dict[str, Any]) -> None:
        severity = str(diagnostics.get("severity") or "")
        if severity == "error":
            self.parser_error_count += 1
            code = str(diagnostics.get("code") or "unknown")
            self.parser_error_codes[code] += 1
        elif severity == "warning":
            self.parser_warning_count += 1
        if diagnostics.get("salvage_applied"):
            self.parser_salvage_count += 1

    def parser_summary(self) -> Dict[str, Any]:
        return {
            "error_count": self.parser_error_count,
            "warning_count": self.parser_warning_count,
            "salvage_count": self.parser_salvage_count,
            "error_codes": dict(self.parser_error_codes),
        }

    def build_task_result(
        self, state: StateT, task_obj: Optional[Task], started_at: float
    ) -> TaskResult:
        stop_reason = state.stop_reason
        success = stop_reason in {
            StopReason.SUCCESS.value,
            StopReason.FINAL.value,
            StopReason.ENV_TERMINAL.value,
        }
        criteria_results = []
        criteria = task_obj.success_criteria if task_obj is not None else []
        for criterion in criteria:
            criteria_results.append(
                TaskCriterionResult(
                    criterion=str(criterion),
                    passed=success,
                    evidence=str(state.final_result or stop_reason or ""),
                )
            )
        workspace = (
            getattr(self.engine.env, "workspace_root", None)
            if self.engine.env is not None
            else None
        )
        artifacts = (
            task_obj.resolve_resources(workspace=workspace)
            if task_obj is not None
            else []
        )
        elapsed_seconds = max(0.0, time.monotonic() - started_at)
        return TaskResult(
            task_id=task_obj.id if task_obj is not None else "",
            success=success,
            stop_reason=stop_reason,
            final_result=state.final_result,
            criteria=criteria_results,
            artifacts=artifacts,
            metrics={
                "steps": len(self.engine.records),
                "elapsed_seconds": elapsed_seconds,
                "token_usage": self.engine._context_runtime.tokens_total,
                "prompt_tokens_total": self.engine._context_runtime.prompt_tokens_total,
                "completion_tokens_total": self.engine._context_runtime.completion_tokens_total,
                "peak_context_occupancy_ratio": self.engine._context_runtime.peak_occupancy_ratio,
            },
            metadata={
                "task_meta": self.task_meta(task_obj),
                "run_meta": self.run_meta(),
            },
        )

    def sanitize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        safe: Dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, dict):
                safe[key] = {
                    str(k): (
                        v
                        if isinstance(v, (str, int, float, bool)) or v is None
                        else repr(v)
                    )
                    for k, v in value.items()
                }
            else:
                safe[key] = repr(value)
        return safe

    def notify_event(self, event: RuntimeEvent, state: StateT) -> None:
        record = None
        if self.engine.records and self.engine.records[-1].step_id == event.step_id:
            record = self.engine.records[-1]
        for hook in self.engine.hooks:
            on_event = getattr(hook, "on_event", None)
            if on_event is None:
                continue
            try:
                on_event(event=event, state=state, record=record, engine=self.engine)
            except Exception as exc:
                _logger.debug("on_event hook raised: %s", exc)
                continue

    def notify_run_start(self, task: str, state: StateT) -> None:
        for hook in self.engine.hooks:
            on_run_start = getattr(hook, "on_run_start", None)
            if on_run_start is None:
                continue
            try:
                on_run_start(task=task, state=state, engine=self.engine)
            except Exception as exc:
                _logger.debug("on_run_start hook raised: %s", exc)
                continue

    def notify_run_end(self, result: Any) -> None:
        for hook in self.engine.hooks:
            on_run_end = getattr(hook, "on_run_end", None)
            if on_run_end is None:
                continue
            try:
                on_run_end(result=result, engine=self.engine)
            except Exception as exc:
                _logger.debug("on_run_end hook raised: %s", exc)
                continue

    def dispatch_hook(self, method_name: str, ctx: HookContext) -> None:
        self.inject_hook_payload(method_name, ctx)
        for hook in self.engine.hooks:
            method = getattr(hook, method_name, None)
            if method is None:
                continue
            try:
                method(ctx=ctx, engine=self.engine)
            except TypeError:
                try:
                    method(ctx, self.engine)
                except Exception as exc:
                    _logger.debug("Hook %s (positional) raised: %s", method_name, exc)
                    continue
            except Exception as exc:
                _logger.debug("Hook %s raised: %s", method_name, exc)
                continue

    def inject_hook_payload(self, method_name: str, ctx: HookContext) -> None:
        now = datetime.now(timezone.utc).isoformat()
        ctx.run_id = self.engine._active_run_id
        if not ctx.ts:
            ctx.ts = now
        payload = dict(ctx.payload or {})
        payload.setdefault("run_id", self.engine._active_run_id)
        payload.setdefault("step_id", ctx.step_id)
        payload.setdefault("phase", ctx.phase.value)
        payload.setdefault("hook", method_name)
        payload.setdefault("task", ctx.task)
        payload.setdefault(
            "stop_reason", ctx.stop_reason or getattr(ctx.state, "stop_reason", None)
        )
        payload.setdefault("ts", ctx.ts)
        payload.setdefault(
            "state_digest",
            {
                "current_step": getattr(ctx.state, "current_step", None),
                "has_final_result": bool(getattr(ctx.state, "final_result", None)),
                "stop_reason": getattr(ctx.state, "stop_reason", None),
            },
        )
        payload.setdefault(
            "decision_digest",
            {
                "mode": (
                    getattr(ctx.decision, "mode", None)
                    if ctx.decision is not None
                    else None
                ),
                "has_actions": (
                    bool(getattr(ctx.decision, "actions", None))
                    if ctx.decision is not None
                    else False
                ),
                "has_final_answer": (
                    bool(getattr(ctx.decision, "final_answer", None))
                    if ctx.decision is not None
                    else False
                ),
            },
        )
        payload.setdefault(
            "action_digest",
            {
                "result_count": len(ctx.action_results or []),
                "tool_invocation_count": (
                    len(getattr(ctx.record, "tool_invocations", []) or [])
                    if ctx.record is not None
                    else 0
                ),
            },
        )
        payload.setdefault("error", str(ctx.error) if ctx.error is not None else None)
        ctx.payload = payload

    def reset_run_state(self) -> None:
        self.engine.events = []
        self.engine.records = []
        self.engine._last_env_observation = None
        self.engine._last_env_result = None

    def clear_active_context(self) -> None:
        self.engine._active_state = None
        self.engine._active_task = ""
        self.engine._active_task_obj = None
        self.engine._last_env_observation = None
        self.engine._last_env_result = None
        self.engine._active_run_id = ""
        self.engine._last_system_prompt = ""
