"""Action executor for QitOS."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core.action import Action, ActionExecutionPolicy, ActionResult, ActionStatus
from ..core.env import Env
from ..core.tool import (
    BaseTool,
    ToolPermissionContext,
    ToolPermissionDecision,
    ToolValidationResult,
)


class ActionExecutor:
    """Executes normalized actions against a tool registry."""

    def __init__(
        self, tool_registry: Any, policy: Optional[ActionExecutionPolicy] = None
    ):
        self.tool_registry = tool_registry
        self.policy = policy or ActionExecutionPolicy()

    def execute(
        self, actions: Sequence[Action], env: Optional[Env] = None, state: Any = None
    ) -> List[ActionResult]:
        if self.policy.mode == "parallel":
            return self._execute_parallel(actions, env=env, state=state)
        return [self._execute_one(action, env=env, state=state) for action in actions]

    def _execute_parallel(
        self, actions: Sequence[Action], env: Optional[Env] = None, state: Any = None
    ) -> List[ActionResult]:
        results: List[ActionResult] = []
        pending_batch: List[Action] = []

        def _flush_batch() -> None:
            nonlocal pending_batch
            if not pending_batch:
                return
            max_workers = min(
                max(1, int(self.policy.max_concurrency)),
                len(pending_batch),
            )
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(self._execute_one, action, env=env, state=state)
                    for action in pending_batch
                ]
                results.extend(future.result() for future in futures)
            pending_batch = []

        for action in actions:
            if self._can_execute_in_parallel(action):
                pending_batch.append(action)
                continue
            _flush_batch()
            results.append(self._execute_one(action, env=env, state=state))

        _flush_batch()
        return results

    def _can_execute_in_parallel(self, action: Action) -> bool:
        tool = self._resolve_tool(action.name)
        if tool is None:
            return False
        spec = getattr(tool, "spec", None)
        if spec is None:
            return False
        return bool(getattr(spec, "read_only", False) and getattr(spec, "concurrency_safe", False))

    def _execute_one(
        self, action: Action, env: Optional[Env] = None, state: Any = None
    ) -> ActionResult:
        start = time.monotonic()
        attempts = 0
        last_error = None
        tool_meta = self._tool_meta(action.name)
        runtime_context = self._build_runtime_context(action.name, env=env, state=state)

        while attempts <= action.max_retries:
            attempts += 1
            try:
                tool = self._resolve_tool(action.name)
                guard_message = self._candidate_submit_ready_guard(action.name, state)
                if guard_message:
                    return self._finish_result(
                        action=action,
                        status=ActionStatus.ERROR,
                        start=start,
                        attempts=attempts,
                        tool_meta=tool_meta,
                        output={
                            "status": "error",
                            "message": guard_message,
                            "error_category": "candidate_submit_ready_guard",
                            "tool": action.name,
                        },
                        error=guard_message,
                        extra_metadata={
                            "error_category": "candidate_submit_ready_guard",
                            "progress_count": len(runtime_context["progress_events"]),
                            "artifacts": list(runtime_context["artifacts"]),
                        },
                    )
                validation = self._validate(tool, action.args, runtime_context)
                if not validation.valid:
                    return self._finish_result(
                        action=action,
                        status=ActionStatus.ERROR,
                        start=start,
                        attempts=attempts,
                        tool_meta=tool_meta,
                        error=validation.message or "tool input validation failed",
                        extra_metadata={
                            "error_category": validation.code or "validation_error",
                            "validation": {
                                "valid": validation.valid,
                                "message": validation.message,
                                "code": validation.code,
                                "suggested_args": validation.suggested_args,
                            },
                        },
                    )

                permission = self._check_permissions(tool, action.args, runtime_context)
                if permission.decision == "deny":
                    return self._finish_result(
                        action=action,
                        status=ActionStatus.SKIPPED,
                        start=start,
                        attempts=attempts,
                        tool_meta=tool_meta,
                        output={
                            "status": "denied",
                            "message": permission.message,
                            "scope": permission.scope,
                        },
                        extra_metadata={
                            "error_category": "permission_denied",
                            "permission": self._permission_payload(permission),
                        },
                    )
                if permission.decision == "ask":
                    return self._finish_result(
                        action=action,
                        status=ActionStatus.SKIPPED,
                        start=start,
                        attempts=attempts,
                        tool_meta=tool_meta,
                        output={
                            "status": "needs_user_input",
                            "message": permission.message,
                            "scope": permission.scope,
                        },
                        extra_metadata={
                            "error_category": "permission_ask",
                            "permission": self._permission_payload(permission),
                        },
                    )

                effective_args = dict(permission.updated_args or action.args)
                output = self._call_tool(
                    tool, action.name, effective_args, runtime_context=runtime_context
                )
                normalized_output = self._normalize_output(tool, output)
                latency = (time.monotonic() - start) * 1000
                return ActionResult(
                    name=action.name,
                    status=ActionStatus.SUCCESS,
                    output=normalized_output,
                    action_id=action.action_id,
                    attempts=attempts,
                    latency_ms=latency,
                    metadata={
                        **tool_meta,
                        "error_category": None,
                        "permission": self._permission_payload(permission),
                        "progress_count": len(runtime_context["progress_events"]),
                        "artifacts": list(runtime_context["artifacts"]),
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive path
                last_error = str(exc)
                if attempts > action.max_retries:
                    break

        error_category = "runtime_error"
        if last_error and "not found" in last_error.lower():
            error_category = "tool_not_found"
        return self._finish_result(
            action=action,
            status=ActionStatus.ERROR,
            start=start,
            attempts=attempts,
            tool_meta=tool_meta,
            error=last_error or "unknown action execution error",
            extra_metadata={
                "error_category": error_category,
                "progress_count": len(runtime_context["progress_events"]),
                "artifacts": list(runtime_context["artifacts"]),
            },
        )

    def _finish_result(
        self,
        *,
        action: Action,
        status: ActionStatus,
        start: float,
        attempts: int,
        tool_meta: Dict[str, Any],
        output: Any = None,
        error: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> ActionResult:
        latency = (time.monotonic() - start) * 1000
        metadata = dict(tool_meta)
        metadata.update(extra_metadata or {})
        return ActionResult(
            name=action.name,
            status=status,
            output=output,
            error=error,
            action_id=action.action_id,
            attempts=attempts,
            latency_ms=latency,
            metadata=metadata,
        )

    def _build_runtime_context(
        self, name: str, env: Optional[Env], state: Any
    ) -> Dict[str, Any]:
        required_ops = self._required_ops(name)
        permission_context = self._resolve_permission_context(env=env, state=state)
        progress_events: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []

        def _emit_progress(payload: Dict[str, Any]) -> None:
            progress_events.append(dict(payload))

        def _record_artifact(payload: Dict[str, Any]) -> None:
            artifacts.append(dict(payload))

        return {
            "env": env,
            "state": state,
            "ops": self._resolve_ops(required_ops, env),
            "tool_registry": self.tool_registry,
            "permission_context": permission_context,
            "progress_events": progress_events,
            "artifacts": artifacts,
            "emit_progress": _emit_progress,
            "record_artifact": _record_artifact,
        }

    def _resolve_tool(self, name: str) -> Optional[BaseTool]:
        if hasattr(self.tool_registry, "get"):
            tool = self.tool_registry.get(name)
            if tool is not None:
                return tool
        return None

    def _validate(
        self,
        tool: Optional[BaseTool],
        args: Dict[str, Any],
        runtime_context: Dict[str, Any],
    ) -> ToolValidationResult:
        if tool is None or not hasattr(tool, "validate_input"):
            return ToolValidationResult.ok()
        result = tool.validate_input(dict(args), runtime_context=runtime_context)
        if isinstance(result, ToolValidationResult):
            return result
        if isinstance(result, dict):
            return ToolValidationResult(
                valid=bool(result.get("valid", result.get("result", True))),
                message=str(result.get("message", "")),
                code=str(result.get("code", result.get("error_code", ""))),
                suggested_args=result.get("suggested_args"),
            )
        if result is False:
            return ToolValidationResult.fail("tool input validation failed")
        return ToolValidationResult.ok()

    def _check_permissions(
        self,
        tool: Optional[BaseTool],
        args: Dict[str, Any],
        runtime_context: Dict[str, Any],
    ) -> ToolPermissionDecision:
        if tool is None or not hasattr(tool, "check_permissions"):
            return ToolPermissionDecision.allow()
        result = tool.check_permissions(dict(args), runtime_context=runtime_context)
        if isinstance(result, ToolPermissionDecision):
            return result
        if isinstance(result, dict):
            return ToolPermissionDecision(
                decision=str(result.get("decision", "allow")),
                message=str(result.get("message", "")),
                scope=str(result.get("scope", "")),
                updated_args=result.get("updated_args"),
            )
        if result in {"allow", "deny", "ask"}:
            return ToolPermissionDecision(decision=str(result))
        return ToolPermissionDecision.allow()

    def _call_tool(
        self,
        tool: Optional[BaseTool],
        name: str,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if tool is not None:
            return tool.call(args, runtime_context=runtime_context)
        if hasattr(self.tool_registry, "call"):
            return self.tool_registry.call(
                name, runtime_context=runtime_context, **args
            )

        if hasattr(self.tool_registry, "get"):
            fallback = self.tool_registry.get(name)
            if fallback is None:
                raise ValueError(f"Unknown tool: {name}")
            if hasattr(fallback, "call"):
                return fallback.call(args, runtime_context=runtime_context)
            if hasattr(fallback, "execute"):
                return fallback.execute(args, runtime_context=runtime_context)
            if hasattr(fallback, "run"):
                return fallback.run(**args)
            return fallback(**args)

        raise TypeError(
            "Unsupported tool registry. Expected object with call() or get()."
        )

    def _candidate_submit_ready_guard(self, name: str, state: Any) -> str:
        if name == "submit_poc":
            return ""
        if not bool(getattr(state, "candidate_ready_for_submit", False)):
            return ""
        poc_path = str(getattr(state, "poc_path", "") or "").strip()
        if not poc_path:
            return ""
        if self._candidate_ready_file_missing(state, poc_path):
            return ""
        return (
            "Candidate is ready for submission. Call submit_poc now; "
            f"{name} is blocked until the ready candidate is submitted."
        )

    @staticmethod
    def _candidate_ready_file_missing(state: Any, poc_path: str) -> bool:
        path = Path(poc_path)
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            workspace_root = str(getattr(state, "workspace_root", "") or "").strip()
            if workspace_root:
                candidates.append(Path(workspace_root) / path)
            candidates.append(path)

        saw_checkable_path = False
        for candidate in candidates:
            try:
                saw_checkable_path = True
                if candidate.is_file():
                    return False
            except OSError:
                continue
        return saw_checkable_path

    def _normalize_output(self, tool: Optional[BaseTool], output: Any) -> Any:
        if tool is None:
            return output
        max_chars = getattr(getattr(tool, "spec", None), "result_max_chars", None)
        if not max_chars or max_chars <= 0:
            return output
        if isinstance(output, str):
            return self._truncate_text(output, max_chars)
        if isinstance(output, dict):
            normalized = dict(output)
            for key in ("content", "stdout", "stderr", "result", "summary", "message"):
                value = normalized.get(key)
                if isinstance(value, str):
                    normalized[key] = self._truncate_text(value, max_chars)
            return normalized
        return output

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... [truncated]"

    def _resolve_permission_context(
        self, env: Optional[Env], state: Any
    ) -> ToolPermissionContext:
        candidate = None
        if state is not None:
            metadata = getattr(state, "metadata", None)
            if isinstance(metadata, dict):
                candidate = metadata.get("tool_permission_context")
        if candidate is None and env is not None:
            candidate = getattr(env, "tool_permission_context", None)
        if isinstance(candidate, ToolPermissionContext):
            return candidate
        if isinstance(candidate, dict):
            return ToolPermissionContext.from_dict(candidate)
        return ToolPermissionContext()

    def _permission_payload(self, decision: ToolPermissionDecision) -> Dict[str, Any]:
        return {
            "decision": decision.decision,
            "message": decision.message,
            "scope": decision.scope,
            "matched_rule": (
                {
                    "effect": decision.matched_rule.effect,
                    "tool_name": decision.matched_rule.tool_name,
                    "tool_family": decision.matched_rule.tool_family,
                    "scope": decision.matched_rule.scope,
                    "message": decision.matched_rule.message,
                }
                if decision.matched_rule is not None
                else None
            ),
        }

    def _required_ops(self, name: str) -> List[str]:
        if hasattr(self.tool_registry, "get"):
            try:
                tool = self.tool_registry.get(name)
                if tool is not None and hasattr(tool, "spec"):
                    spec = getattr(tool, "spec")
                    if hasattr(spec, "required_ops"):
                        value = getattr(spec, "required_ops")
                        if isinstance(value, list):
                            return [str(x) for x in value]
            except Exception:
                return []
        return []

    def _resolve_ops(
        self, required_ops: List[str], env: Optional[Env]
    ) -> Dict[str, Any]:
        if not required_ops:
            return {}
        if env is None:
            raise ValueError(
                f"Tool requires ops {required_ops} but no env was provided"
            )
        out: Dict[str, Any] = {}
        for group in required_ops:
            ops = env.get_ops(group)
            if ops is None:
                raise ValueError(
                    f"Env '{getattr(env, 'name', 'env')}' missing required ops group: {group}"
                )
            out[group] = ops
        return out

    def _tool_meta(self, name: str) -> dict[str, Any]:
        if hasattr(self.tool_registry, "describe_tool"):
            try:
                desc = self.tool_registry.describe_tool(name)
                origin = desc.get("origin", {})
                return {
                    "tool_name": desc.get("name", name),
                    "toolset_name": origin.get("toolset_name"),
                    "toolset_version": origin.get("toolset_version"),
                    "source": origin.get("source", "function"),
                }
            except Exception:
                pass
        return {
            "tool_name": name,
            "toolset_name": None,
            "toolset_version": None,
            "source": "unknown",
        }
