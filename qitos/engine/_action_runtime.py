"""Private action execution helpers for Engine."""

from __future__ import annotations

import json
from typing import Any, Dict, Generic, List, Optional, TypeVar, cast

from ..core.action import Action
from ..core.decision import Decision
from ..core.tool_result import ToolResult
from ._protocol import _EngineProtocol
from .states import RuntimePhase, StepRecord


StateT = TypeVar("StateT")
ActionT = TypeVar("ActionT")


class _ActionRuntime(Generic[StateT, ActionT]):
    def __init__(self, engine: _EngineProtocol):
        self.engine = engine

    @staticmethod
    def _tool_output_for_budget(output: Any) -> tuple[Any, bool]:
        has_summary = (
            isinstance(output, dict)
            and isinstance(output.get("model_summary"), str)
            and bool(output["model_summary"].strip())
        )
        return (output["model_summary"].strip(), True) if has_summary else (output, False)

    def run_act(
        self, state: StateT, decision: Decision[ActionT], record: StepRecord
    ) -> List[Any]:
        engine = self.engine
        engine._dispatch_hook(
            "on_before_act",
            engine._hook_context(
                step_id=record.step_id,
                phase=RuntimePhase.ACT,
                state=state,
                decision=decision,
                record=record,
            ),
        )
        engine._emit(record.step_id, RuntimePhase.ACT, payload={"stage": "start"})

        if decision.mode != "act":
            engine._emit(
                record.step_id,
                RuntimePhase.ACT,
                payload={"stage": "skipped", "reason": "decision_not_act"},
            )
            return []
        if engine.executor is None:
            raise RuntimeError("No tool registry configured for action execution")

        actions: List[Action] = []
        for action in decision.actions:
            if isinstance(action, Action):
                # Check for handoff tool interception
                handoff = engine._intercept_handoff_action(action)
                if handoff is not None:
                    return handoff
                actions.append(action)
                continue
            payload = (
                action if isinstance(action, dict) else cast(Dict[str, Any], action)
            )
            normalized = Action.from_dict(payload)
            # Check for handoff tool interception
            handoff = engine._intercept_handoff_action(normalized)
            if handoff is not None:
                return handoff
            actions.append(normalized)
        # Pre-flight checks: collect blocked/loop-blocked actions, execute the rest
        blocked_indices: set[int] = set()
        blocked_results: List[tuple[int, ToolResult]] = []
        blocked_invocations: List[tuple[int, Dict[str, Any]]] = []
        for i, normalized_action in enumerate(actions):
            engine._memory_append("action", normalized_action, record.step_id)
            block_reason = self._action_block_reason(state, normalized_action)
            if block_reason:
                blocked_result = ToolResult(
                    status="error",
                    output={
                        "status": "blocked",
                        "message": block_reason,
                        "tool_name": normalized_action.name,
                    },
                    error="action_blocked",
                    metadata={
                        "tool_name": normalized_action.name,
                        "error_category": "action_blocked",
                    },
                )
                blocked_indices.add(i)
                blocked_results.append((i, blocked_result))
                blocked_invocations.append((i, {
                    "tool_name": normalized_action.name,
                    "action_id": normalized_action.action_id,
                    "args": dict(normalized_action.args or {}),
                    "toolset_name": None,
                    "toolset_version": None,
                    "source": "agent_action_gate",
                    "attempts": 0,
                    "latency_ms": 0,
                    "status": "error",
                    "error_category": "action_blocked",
                    "error": "action_blocked",
                }))
                engine._memory_append("action_result", blocked_result, record.step_id)
                if self._history_tool_calls_enabled(record):
                    tool_call_id = normalized_action.action_id or f"call_{record.step_id}_{i}"
                    engine._history_append(
                        "tool",
                        self._serialize_for_tool_message(
                            blocked_result.output,
                            blocked_result.error,
                        ),
                        record.step_id,
                        metadata={
                            "source": "engine",
                            "tool_name": normalized_action.name,
                        },
                        tool_call_id=tool_call_id,
                        name=normalized_action.name,
                    )
                else:
                    # When a custom MessageBuilder is active, avoid injecting
                    # synthetic user messages for blocked actions.  The
                    # block_reason is already carried in the ToolResult.
                    _has_custom_builder = getattr(
                        getattr(engine, 'agent', None), 'message_builder', None
                    ) is not None
                    if not _has_custom_builder:
                        engine._history_append(
                            "user",
                            block_reason,
                            record.step_id,
                            metadata={
                                "source": "action_gate",
                                "tool_name": normalized_action.name,
                            },
                        )
                engine._emit(
                    record.step_id,
                    RuntimePhase.ACT,
                    payload={
                        "stage": "action_blocked",
                        "tool_name": normalized_action.name,
                        "reason": block_reason,
                        "action_results": [
                            self._model_visible_tool_result_dict(
                                blocked_result,
                                normalized_action.name,
                            )
                        ],
                    },
                )
                continue
            loop_result = engine._tool_loop_detector.check_detailed(
                normalized_action.name, normalized_action.args
            )
            if loop_result.level == "block":
                loop_tool_result = ToolResult(
                    status="error",
                    output={
                        "status": "blocked",
                        "message": loop_result.message,
                        "tool_name": normalized_action.name,
                    },
                    error="tool_call_loop_detected",
                    metadata={
                        "tool_name": normalized_action.name,
                        "reason": loop_result.message,
                    },
                )
                blocked_indices.add(i)
                blocked_results.append((i, loop_tool_result))
                blocked_invocations.append((i, {
                    "tool_name": normalized_action.name,
                    "action_id": normalized_action.action_id,
                    "args": dict(normalized_action.args or {}),
                    "toolset_name": None,
                    "toolset_version": None,
                    "source": "loop_detector",
                    "attempts": 0,
                    "latency_ms": 0,
                    "status": "error",
                    "error_category": "tool_call_loop_detected",
                    "error": "tool_call_loop_detected",
                }))
                if self._history_tool_calls_enabled(record):
                    tool_call_id = normalized_action.action_id or f"call_{record.step_id}_{i}"
                    engine._history_append(
                        "tool",
                        self._serialize_for_tool_message(loop_tool_result.output, loop_tool_result.error),
                        record.step_id,
                        metadata={"source": "loop_detector", "tool_name": normalized_action.name},
                        tool_call_id=tool_call_id,
                        name=normalized_action.name,
                    )
                engine._emit(
                    record.step_id,
                    RuntimePhase.ACT,
                    payload={
                        "stage": "tool_call_loop_detected",
                        "tool_name": normalized_action.name,
                        "recovery_message": loop_result.message,
                    },
                )
                continue
            elif loop_result.level == "warn":
                # Do not inject a synthetic user turn between a tool call and
                # its result. The event remains visible to tracing/TUI only.
                engine._emit(
                    record.step_id,
                    RuntimePhase.ACT,
                    payload={
                        "stage": "tool_call_loop_warning",
                        "tool_name": normalized_action.name,
                        "recovery_message": loop_result.message,
                    },
                )

        # If all actions were blocked, return immediately
        if len(blocked_indices) == len(actions):
            merged_results = [br for _, br in sorted(blocked_results, key=lambda x: x[0])]
            merged_invocations = [bi for _, bi in sorted(blocked_invocations, key=lambda x: x[0])]
            record.action_results = merged_results
            record.tool_invocations = merged_invocations
            engine._dispatch_hook(
                "on_after_act",
                engine._hook_context(
                    step_id=record.step_id,
                    phase=RuntimePhase.ACT,
                    state=state,
                    decision=decision,
                    action_results=[r.to_dict() for r in merged_results],
                    record=record,
                ),
            )
            return [r.to_dict() for r in merged_results]

        # Execute non-blocked actions
        executable_actions = [a for i, a in enumerate(actions) if i not in blocked_indices]
        executable_indices = [i for i in range(len(actions)) if i not in blocked_indices]
        execution = engine.executor.execute(executable_actions, env=engine.env, state=state)
        # Build tool_invocations from execution results (executable only)
        exec_invocations = [
            {
                "tool_name": item.name,
                "action_id": executable_actions[index].action_id if index < len(executable_actions) else "",
                "args": dict(executable_actions[index].args or {}) if index < len(executable_actions) else {},
                "toolset_name": item.metadata.get("toolset_name"),
                "toolset_version": item.metadata.get("toolset_version"),
                "source": item.metadata.get("source"),
                "attempts": item.attempts,
                "latency_ms": item.latency_ms,
                "status": item.status.value,
                "error_category": item.metadata.get("error_category"),
                "error": item.error,
            }
            for index, item in enumerate(execution)
        ]
        results: List[ToolResult] = []
        max_chars = int(getattr(engine.context_config, "tool_result_max_chars", 0) or 0)
        per_message_max = int(getattr(engine.context_config, "tool_result_per_message_max_chars", 0) or 0)
        message_total_chars = 0
        for item in execution:
            if item.status.value == "success":
                output = item.output
                output_status = ""
                output_error = None
                if isinstance(output, dict):
                    output_status = str(output.get("status") or "").strip().lower()
                    output_error = output.get("error") or output.get("message")
                if output_status in {"error", "failed", "denied", "needs_user_input"}:
                    results.append(
                        ToolResult(
                            status="error",
                            output=output,
                            error=str(output_error or output_status),
                            metadata={
                                "tool_name": item.name,
                                "latency_ms": item.latency_ms,
                                "attempts": item.attempts,
                            },
                        )
                    )
                    continue
                # Truncate large tool results to prevent context overflow
                if max_chars > 0 and output is not None:
                    # Artifact-heavy tools may expose a bounded, human-readable
                    # model projection while retaining their canonical structured
                    # result for reducers and trace replay.  Budget and truncate
                    # that projection, not the raw dict.  Converting the raw dict
                    # to a truncated string here used to discard ``model_summary``
                    # before _model_visible_tool_output() could select it, so
                    # sufficiently large STATIC/GDB results leaked JSON into the
                    # provider history while smaller results rendered correctly.
                    visible_for_budget, has_model_summary = self._tool_output_for_budget(output)
                    output_str = (
                        visible_for_budget
                        if isinstance(visible_for_budget, str)
                        else json.dumps(visible_for_budget, ensure_ascii=False, default=str)
                    )
                    # Per-message aggregate budget: if total exceeds limit, apply stricter per-tool truncation
                    effective_max = max_chars
                    if per_message_max > 0 and message_total_chars + len(output_str) > per_message_max:
                        # Reduce per-tool limit to fit within aggregate budget
                        remaining = max(0, per_message_max - message_total_chars)
                        effective_max = min(max_chars, remaining)
                    if len(output_str) > effective_max and not has_model_summary:
                        head = int(effective_max * 0.7)
                        tail = effective_max - head
                        truncated = output_str[:head] + f"\n... [truncated, {len(output_str)} chars total] ...\n" + output_str[-tail:]
                        output = truncated
                        message_total_chars += len(output) if isinstance(output, str) else 0
                    else:
                        message_total_chars += len(output_str)
                results.append(
                    ToolResult(
                        status="success",
                        output=output,
                        metadata={
                            "tool_name": item.name,
                            "latency_ms": item.latency_ms,
                            "attempts": item.attempts,
                        },
                    )
                )
            else:
                results.append(
                    ToolResult(
                        status="error",
                        output=None,
                        error=str(item.error or "tool execution failed"),
                        metadata={
                            "tool_name": item.name,
                            "latency_ms": item.latency_ms,
                            "attempts": item.attempts,
                        },
                    )
                )

        # Merge blocked results and execution results back into original action order
        if blocked_indices:
            # Map execution result indices to original action indices
            exec_result_by_orig_idx: Dict[int, ToolResult] = {}
            for exec_i, orig_i in enumerate(executable_indices):
                if exec_i < len(results):
                    exec_result_by_orig_idx[orig_i] = results[exec_i]
            blocked_result_by_orig_idx: Dict[int, ToolResult] = {idx: r for idx, r in blocked_results}
            blocked_inv_by_orig_idx: Dict[int, Dict[str, Any]] = {idx: inv for idx, inv in blocked_invocations}
            exec_inv_by_orig_idx: Dict[int, Dict[str, Any]] = {}
            for exec_i, orig_i in enumerate(executable_indices):
                if exec_i < len(exec_invocations):
                    exec_inv_by_orig_idx[orig_i] = exec_invocations[exec_i]

            merged_results: List[ToolResult] = []
            merged_invocations: List[Dict[str, Any]] = []
            for i in range(len(actions)):
                if i in blocked_indices:
                    merged_results.append(blocked_result_by_orig_idx.get(i, ToolResult(status="error", output=None, error="action_blocked")))
                    merged_invocations.append(blocked_inv_by_orig_idx.get(i, {}))
                else:
                    merged_results.append(exec_result_by_orig_idx.get(i, ToolResult(status="error", output=None, error="execution_failed")))
                    merged_invocations.append(exec_inv_by_orig_idx.get(i, {}))
            results = merged_results
            record.tool_invocations = merged_invocations
        else:
            record.tool_invocations = exec_invocations

        # Optional agent-owned pre-history commit for model-visible state
        # receipts.  This is intentionally generic: an agent may canonicalize
        # a state-tool result before history/TUI serialization while the
        # normal reduce pass remains responsible for trace projection.  It is
        # executed once in original tool-call order.
        commit_results = getattr(getattr(engine, "agent", None), "commit_action_results", None)
        if callable(commit_results):
            commit_results(state, actions, results, step_id=record.step_id)

        if engine.env is not None:
            env_result = engine._run_env_step(
                decision=decision,
                action_results=[item.to_dict() for item in results],
            )
            if env_result is not None:
                results.append(
                    ToolResult(
                        status="success",
                        output={"env": engine._env_step_result_to_dict(env_result)},
                        metadata={"source": "env"},
                    )
                )
        record.action_results = results
        for item in results:
            engine._memory_append("action_result", item, record.step_id)
        for normalized_action in executable_actions:
            engine._tool_loop_detector.record(
                normalized_action.name, dict(normalized_action.args or {})
            )

        if self._history_tool_calls_enabled(record):
            for idx, result in enumerate(results):
                payload = result.output
                if isinstance(payload, dict) and set(payload.keys()) == {"env"}:
                    continue
                tool_name = actions[idx].name if idx < len(actions) else ""
                tool_call_id = None
                if idx < len(actions):
                    tool_call_id = actions[idx].action_id
                if not tool_call_id:
                    tool_call_id = f"call_{record.step_id}_{idx}"
                model_payload = self._model_visible_tool_output(tool_name, payload)
                serialized = self._serialize_for_tool_message(model_payload, result.error)
                engine._history_append(
                    "tool",
                    serialized[
                        : max(256, int(getattr(engine.context_config, "tool_result_max_chars", 4000)))
                    ],
                    record.step_id,
                    metadata={"source": "engine", "tool_name": tool_name},
                    tool_call_id=tool_call_id,
                    name=(tool_name or None),
                )
        engine._emit(
            record.step_id,
            RuntimePhase.ACT,
            payload={
                "stage": "action_results",
                "tool_invocations": record.tool_invocations,
                "action_results": [
                    self._model_visible_tool_result_dict(
                        item,
                        actions[idx].name if idx < len(actions) else "",
                    )
                    for idx, item in enumerate(results)
                ],
            },
        )
        engine._dispatch_hook(
            "on_after_act",
            engine._hook_context(
                step_id=record.step_id,
                phase=RuntimePhase.ACT,
                state=state,
                decision=decision,
                # Keep every human-visible surface on the exact same projection
                # as native provider history.  ``record.action_results`` remains
                # canonical so reducers and trace replay retain the structured
                # machine contract; hooks drive tui.log and must not bypass a
                # tool's model_summary (notably STATIC_* and gdb_debug).
                action_results=[
                    self._model_visible_tool_result_dict(
                        item,
                        actions[idx].name if idx < len(actions) else "",
                    )
                    for idx, item in enumerate(results)
                ],
                record=record,
            ),
        )
        return [item.to_dict() for item in results]

    def _serialize_for_tool_message(self, output: Any, error: str | None) -> str:
        payload = output if error in (None, "") else {"error": str(error), "output": output}
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            return str(payload)

    @staticmethod
    def _history_tool_calls_enabled(record: Any) -> bool:
        return bool(
            getattr(record, "native_tool_call_used", False)
            or getattr(record, "history_tool_calls_pending", False)
        )

    def _action_block_reason(self, state: StateT, action: Action) -> str:
        blocker = getattr(self.engine.agent, "block_action", None)
        if blocker is None:
            return ""
        try:
            reason = blocker(state, action)
        except TypeError:
            reason = blocker(action)
        except Exception:
            return ""
        return str(reason or "").strip()

    def _model_visible_tool_output(self, tool_name: str, output: Any) -> Any:
        """Project a bounded tool summary into native tool-call history.

        Reducers and trace writers retain the canonical structured result. A
        tool may additionally provide ``model_summary`` when the raw result is
        an artifact-heavy machine contract whose useful facts need a compact
        model-facing representation. This is intentionally generic: it is not
        a benchmark-specific rendering path.
        """
        short_name = str(tool_name).rsplit(".", 1)[-1]
        # The verifier projection is a privacy boundary. It takes precedence
        # over any accidental tool-provided summary.
        if short_name in {"submit_poc", "SUBMIT"}:
            if not isinstance(output, dict):
                return output
            if output.get("status") == "error":
                visible_error = {
                    "summary": output.get("summary"),
                    "status": "error",
                    "error": output.get("error") or output.get("raw_output") or "submission failed",
                    "poc_path": output.get("poc_path"),
                }
                if short_name == "SUBMIT":
                    visible_error["verification_status"] = output.get("verification_status")
                    visible_error["oracle_outcome"] = output.get("oracle_outcome")
                return {key: value for key, value in visible_error.items() if value not in (None, "")}
            visible = {
                "summary": output.get("summary"),
                "status": output.get("status"),
                "poc_id": output.get("poc_id"),
                "flag": output.get("flag"),
                "exit_code": output.get("vul_exit_code", output.get("exit_code")),
                "output": output.get("raw_output", ""),
                "stderr": output.get("vul_stderr", ""),
                "stdout": output.get("vul_stdout", ""),
            }
            if short_name == "SUBMIT":
                visible["verification_status"] = output.get("verification_status")
                visible["verification_scope"] = output.get("verification_scope")
                visible["oracle_outcome"] = output.get("oracle_outcome")
            return {key: value for key, value in visible.items() if value not in (None, "")}
        if isinstance(output, dict) and isinstance(output.get("model_summary"), str):
            summary = output["model_summary"].strip()
            if summary:
                return summary
        if short_name not in {"submit_poc", "SUBMIT"}:
            return output
        return output

    def _model_visible_tool_result_dict(
        self,
        result: ToolResult,
        tool_name: str,
    ) -> Dict[str, Any]:
        payload = result.to_dict()
        short_name = str(tool_name).rsplit(".", 1)[-1]
        has_summary = isinstance(result.output, dict) and bool(
            str(result.output.get("model_summary") or "").strip()
        )
        if short_name not in {"submit_poc", "SUBMIT"} and not has_summary:
            return payload
        visible_output = self._model_visible_tool_output(tool_name, result.output)
        visible = ToolResult(
            status=result.status,
            output=visible_output,
            error=result.error,
            metadata=dict(result.metadata),
        ).to_dict()
        visible["metadata"] = {
            **dict(visible.get("metadata") or {}),
            "model_visible": True,
        }
        return visible
