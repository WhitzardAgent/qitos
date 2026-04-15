"""Private action execution helpers for Engine."""

from __future__ import annotations

import json
from typing import Any, Dict, Generic, List, TypeVar, cast

from ..core.action import Action
from ..core.decision import Decision
from ..core.tool_result import ToolResult
from .states import RuntimePhase, StepRecord


StateT = TypeVar("StateT")
ActionT = TypeVar("ActionT")


class _ActionRuntime(Generic[StateT, ActionT]):
    def __init__(self, engine: Any):
        self.engine = engine

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
                actions.append(action)
                continue
            payload = (
                action if isinstance(action, dict) else cast(Dict[str, Any], action)
            )
            actions.append(Action.from_dict(payload))
        for normalized_action in actions:
            engine._memory_append("action", normalized_action, record.step_id)
            recovery_message = engine._tool_loop_detector.check(
                normalized_action.name, normalized_action.args
            )
            if recovery_message:
                loop_result = ToolResult(
                    status="error",
                    output=None,
                    error="tool_call_loop_detected",
                    metadata={
                        "tool_name": normalized_action.name,
                        "reason": recovery_message,
                    },
                )
                record.action_results = [loop_result]
                engine._history_append(
                    "user",
                    recovery_message,
                    record.step_id,
                    metadata={"source": "loop_detector"},
                )
                engine._emit(
                    record.step_id,
                    RuntimePhase.ACT,
                    payload={
                        "stage": "tool_call_loop_detected",
                        "tool_name": normalized_action.name,
                        "recovery_message": recovery_message,
                    },
                )
                return [loop_result.to_dict()]

        execution = engine.executor.execute(actions, env=engine.env, state=state)
        record.tool_invocations = [
            {
                "tool_name": item.name,
                "toolset_name": item.metadata.get("toolset_name"),
                "toolset_version": item.metadata.get("toolset_version"),
                "source": item.metadata.get("source"),
                "attempts": item.attempts,
                "latency_ms": item.latency_ms,
                "status": item.status.value,
                "error_category": item.metadata.get("error_category"),
                "error": item.error,
            }
            for item in execution
        ]
        results: List[ToolResult] = []
        for item in execution:
            if item.status.value == "success":
                results.append(
                    ToolResult(
                        status="success",
                        output=item.output,
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
        for normalized_action in actions:
            engine._tool_loop_detector.record(
                normalized_action.name, dict(normalized_action.args or {})
            )

        if record.decision_source == "native_tool_calls" and record.native_tool_call_used:
            for idx, result in enumerate(results):
                payload = result.output
                if isinstance(payload, dict) and set(payload.keys()) == {"env"}:
                    continue
                tool_call_id = None
                if idx < len(actions):
                    tool_call_id = actions[idx].action_id
                if not tool_call_id:
                    tool_call_id = f"call_{record.step_id}_{idx}"
                serialized = self._serialize_for_tool_message(payload, result.error)
                engine._history_append(
                    "tool",
                    serialized[
                        : max(256, int(getattr(engine.context_config, "tool_result_max_chars", 4000)))
                    ],
                    record.step_id,
                    metadata={"source": "engine", "tool_name": actions[idx].name if idx < len(actions) else ""},
                    tool_call_id=tool_call_id,
                    name=(actions[idx].name if idx < len(actions) else None),
                )
        engine._emit(
            record.step_id,
            RuntimePhase.ACT,
            payload={
                "stage": "action_results",
                "tool_invocations": record.tool_invocations,
                "action_results": [item.to_dict() for item in results],
            },
        )
        engine._dispatch_hook(
            "on_after_act",
            engine._hook_context(
                step_id=record.step_id,
                phase=RuntimePhase.ACT,
                state=state,
                decision=decision,
                action_results=[item.to_dict() for item in results],
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
