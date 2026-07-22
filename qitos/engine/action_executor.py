"""Action executor for QitOS."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from ..core.action import Action, ActionExecutionPolicy, ActionResult, ActionStatus
from ..core.env import Env
from ..core.interceptor import InterceptorChain, InterceptorContext
from ..core.tool import (
    BaseTool,
    ToolPermissionContext,
    ToolPermissionDecision,
    ToolValidationResult,
)
from .states import RuntimePhase

if TYPE_CHECKING:
    from ._protocol import _EngineProtocol


# Tools that are safe to run concurrently (read-only, no side effects)
_CONCURRENCY_SAFE_TOOLS = frozenset({
    "file_read_v2", "read_file", "Read", "view",
    "Glob", "Grep", "glob_v2", "grep_v2",
    "WebFetch", "web_fetch_v2",
    "task_list", "task_get",
    # CyberGym read-only tools
    "READ", "GREP", "FindSymbols", "CallsiteSearch", "RepoMap",
    "FileInfo", "HexView", "StructProbe", "CorpusInspect",
})


class ActionExecutor:
    """Executes normalized actions against a tool registry."""

    def __init__(
        self,
        tool_registry: Any,
        policy: Optional[ActionExecutionPolicy] = None,
        trace_writer: Any = None,
        delegate_depth: int = 0,
        shared_memory: Any = None,
        engine: Optional[_EngineProtocol] = None,
        permission_pipeline: Any = None,
        read_before_write_enforcer: Any = None,
        permission_interaction_callback: Optional[Any] = None,
        interceptor_chain: Optional[InterceptorChain] = None,
        auto_approve: bool = False,
    ):
        self.tool_registry = tool_registry
        self.policy = policy or ActionExecutionPolicy()
        self.trace_writer = trace_writer
        self.delegate_depth = delegate_depth
        self.shared_memory = shared_memory
        self._engine = engine
        self._pipeline = permission_pipeline
        self._rbw_enforcer = read_before_write_enforcer
        self._permission_interaction_callback = permission_interaction_callback
        self._interceptor_chain = interceptor_chain
        self.auto_approve = auto_approve

    def execute(
        self, actions: Sequence[Action], env: Optional[Env] = None, state: Any = None
    ) -> List[ActionResult]:
        if not actions:
            return []

        # Single action: execute directly
        if len(actions) == 1:
            return [self._execute_one(actions[0], env=env, state=state)]

        # Respect ActionExecutionPolicy.mode
        if self.policy.mode == "serial":
            return [self._execute_one(a, env=env, state=state) for a in actions]

        # Auto/parallel mode: classify and run safe tools in parallel
        safe_indices, exclusive_indices = self._classify_actions(actions)
        if len(safe_indices) >= 2:
            return self._execute_concurrent(
                actions, env=env, state=state,
                safe_indices=safe_indices,
                exclusive_indices=exclusive_indices,
            )

        # All exclusive or only one safe: run sequentially
        return [self._execute_one(action, env=env, state=state) for action in actions]

    def _classify_actions(
        self, actions: Sequence[Action]
    ) -> Tuple[List[int], List[int]]:
        """Classify actions into concurrency-safe and exclusive."""
        safe_indices: List[int] = []
        exclusive_indices: List[int] = []
        for i, action in enumerate(actions):
            if self._is_concurrency_safe(action.name):
                safe_indices.append(i)
            else:
                exclusive_indices.append(i)
        return safe_indices, exclusive_indices

    def _is_concurrency_safe(self, tool_name: str) -> bool:
        """Check if a tool is safe to run concurrently.

        Priority:
        1. Tools with needs_approval=True are NEVER concurrency safe
        2. ToolSpec.concurrency_safe=True → safe
        3. ToolSpec.read_only=True → safe
        4. Fallback to legacy _CONCURRENCY_SAFE_TOOLS set
        """
        allowed = self.policy.parallel_tool_names
        if allowed is not None and tool_name not in allowed:
            return False
        tool = self._resolve_tool(tool_name)
        if tool is not None and hasattr(tool, "spec"):
            spec = tool.spec
            # Tools needing approval are NEVER concurrency safe
            if getattr(spec, "needs_approval", False):
                return False
            concurrency_safe = getattr(spec, "concurrency_safe", None)
            if concurrency_safe is True:
                return True
            if concurrency_safe is False:
                return False
            # A read-only tool without an explicit concurrency declaration is
            # safe by default; an explicit False remains authoritative.
            if getattr(spec, "read_only", False) is True:
                return True
        # Fallback: check legacy hardcoded set
        return tool_name in _CONCURRENCY_SAFE_TOOLS

    def _execute_concurrent(
        self,
        actions: Sequence[Action],
        env: Optional[Env] = None,
        state: Any = None,
        safe_indices: List[int] | None = None,
        exclusive_indices: List[int] | None = None,
    ) -> List[ActionResult]:
        """Execute actions with concurrency-safe tools running in parallel.

        Read-only/concurrency-safe tools are run concurrently.
        Exclusive tools (write, approval-gated) are run sequentially.
        Results are returned in the original order.
        """
        if safe_indices is None or exclusive_indices is None:
            safe_indices, exclusive_indices = self._classify_actions(actions)

        # If all actions are exclusive or only one safe action, run sequentially
        if len(safe_indices) <= 1:
            return [self._execute_one(action, env=env, state=state) for action in actions]

        # Run safe actions in parallel, exclusive actions sequentially
        results: List[Optional[ActionResult]] = [None] * len(actions)

        max_workers = min(
            max(1, self.policy.max_concurrency),
            len(safe_indices),
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: Dict[Any, int] = {}
            for idx in safe_indices:
                future = pool.submit(self._execute_one, actions[idx], env=env, state=state)
                futures[future] = idx
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                # fail_fast: cancel remaining on first error
                if self.policy.fail_fast and results[idx] is not None:
                    if getattr(results[idx], "status", None) == ActionStatus.ERROR:
                        for pending in list(futures.keys()):
                            if not pending.done():
                                pending.cancel()
                        break

        # Execute exclusive (write/bash/approval) actions sequentially
        for idx in exclusive_indices:
            results[idx] = self._execute_one(actions[idx], env=env, state=state)

        # All slots should be filled
        return [r if r is not None else self._error_result(actions[i], "concurrent_execution_failed") for i, r in enumerate(results)]

    def _error_result(self, action: Action, message: str) -> ActionResult:
        """Create an error ActionResult for a failed concurrent execution slot."""
        card = "\n".join(
            [
                "[TOOL_RESULT_MISSING]",
                "",
                f"Tool: `{action.name}`",
                "Code: `TOOL_RESULT_MISSING`",
                "",
                "The executor did not produce a result. No success was inferred.",
                "Retry the call or choose another distinguishable action.",
            ]
        )
        return ActionResult(
            name=action.name,
            status=ActionStatus.ERROR,
            output=card,
            error=message,
            action_id=action.action_id,
            attempts=1,
            latency_ms=0.0,
            metadata={"error_category": "concurrent_execution_error"},
        )

    def _execute_one(
        self, action: Action, env: Optional[Env] = None, state: Any = None
    ) -> ActionResult:
        start = time.monotonic()
        attempts = 0
        last_error = None
        tool_meta = self._tool_meta(action.name)
        runtime_context = self._build_runtime_context(action.name, env=env, state=state)

        # Resolve per-tool retry_policy and on_failure from tool spec
        _retry_policy = None
        _on_failure = None
        available = (
            [
                str(item)
                for item in list(self.tool_registry.list_tools() or [])
                if str(item)
            ]
            if hasattr(self.tool_registry, "list_tools")
            else []
        )
        # Model-originated tool names are an exact protocol contract.  The
        # registry may support aliases for host integrations, but execution
        # must not silently repair casing or parse argument fragments embedded
        # in a malformed name.
        if available and action.name not in available:
            card = "\n".join(
                [
                    "[TOOL:unknown]",
                    "",
                    f"Unknown tool: `{action.name}`",
                    "",
                    "No tool was executed.",
                    "",
                    "Available tools:",
                    ", ".join(f"`{item}`" for item in available),
                    "",
                    "Retry using an exact tool name and its declared schema.",
                ]
            )
            return self._finish_result(
                action=action,
                status=ActionStatus.ERROR,
                start=start,
                attempts=1,
                tool_meta=tool_meta,
                output=card,
                error=f"Unknown tool: {action.name}",
                extra_metadata={
                    "error_category": "tool_not_found",
                    "raw_tool_name": action.name,
                    "raw_arguments": dict(action.args),
                    "available_tools": available,
                    "recoverable": True,
                    "executed": False,
                },
            )
        tool_preview = self._resolve_tool(action.name)
        if tool_preview is not None and hasattr(tool_preview, 'spec'):
            _retry_policy = getattr(tool_preview.spec, 'retry_policy', None)
            _on_failure = getattr(tool_preview.spec, 'on_failure', None)

        # 1. Interceptor before_execute — can modify action args
        interceptor_context = InterceptorContext(
            tool_name=action.name,
            tool_args=dict(action.args),
            step_id=getattr(state, "current_step", 0) if state else 0,
            state=self._engine,
            run_id=getattr(self._engine, "_active_run_id", "") if self._engine else "",
        )
        if self._interceptor_chain is not None:
            action = self._interceptor_chain.before_execute(action, interceptor_context)

        # 2. Check needs_approval — triggers interrupt() for human approval
        _auto_approved = False
        if tool_preview is not None and hasattr(tool_preview, 'spec'):
            _needs_approval_val = getattr(tool_preview.spec, 'needs_approval', False)
            if _needs_approval_val:
                if callable(_needs_approval_val) and not isinstance(_needs_approval_val, bool):
                    _needs_approval_val = _needs_approval_val(runtime_context, action.args)
            if _needs_approval_val:
                if self.auto_approve:
                    _auto_approved = True
                else:
                    from ..engine.interrupt import interrupt
                    from ..engine.approval import ToolApprovalItem

                    approval_item = ToolApprovalItem(
                        tool_name=action.name,
                        tool_args=action.args,
                        message=f"Tool '{action.name}' requires approval before execution.",
                    )
                    approval = interrupt(approval_item)
                    if approval == "deny":
                        return self._finish_result(
                            action=action,
                            status=ActionStatus.SKIPPED,
                            start=start,
                            attempts=1,
                            tool_meta=tool_meta,
                            output={"status": "denied", "message": "User denied approval"},
                            extra_metadata={"error_category": "approval_denied"},
                        )

        # Compute effective max attempts from retry_policy or fallback to max_retries
        if _retry_policy is not None:
            _max_attempts = _retry_policy.max_attempts
            _backoff_factor = _retry_policy.backoff_factor
            _max_backoff = _retry_policy.max_backoff
            _jitter = _retry_policy.jitter
            _retryable_exceptions = _retry_policy.retryable_exceptions
        else:
            _max_attempts = action.max_retries + 1  # existing behavior
            _backoff_factor = 0
            _max_backoff = 0
            _jitter = False
            _retryable_exceptions = (Exception,)

        while attempts < _max_attempts:
            attempts += 1
            try:
                tool = self._resolve_tool(action.name)
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

                # Read-before-write check for file editing tools
                rbw_blocked = self._check_read_before_write(action)
                if rbw_blocked is not None:
                    return rbw_blocked

                permission = self._check_permissions(tool, action.args, runtime_context)
                if permission.decision == "deny":
                    self._dispatch_tool_hook(
                        "on_permission_denied", action.name, action.args,
                        tool_result=None, permission_decision="deny",
                    )
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
                    # Try interactive resolution if callback is set
                    if self._permission_interaction_callback is not None:
                        try:
                            user_decision = self._permission_interaction_callback(
                                tool_name=action.name,
                                args=action.args,
                                permission=permission,
                            )
                            if user_decision == "allow":
                                permission = ToolPermissionDecision.allow()
                            elif user_decision == "deny":
                                self._dispatch_tool_hook(
                                    "on_permission_denied", action.name, action.args,
                                    tool_result=None, permission_decision="deny",
                                )
                                return self._finish_result(
                                    action=action,
                                    status=ActionStatus.SKIPPED,
                                    start=start,
                                    attempts=attempts,
                                    tool_meta=tool_meta,
                                    output={
                                        "status": "denied",
                                        "message": "User denied permission",
                                        "scope": permission.scope,
                                    },
                                    extra_metadata={
                                        "error_category": "permission_denied",
                                        "permission": self._permission_payload(permission),
                                    },
                                )
                            # else: fall through to SKIPPED
                        except Exception:
                            pass  # Callback failed, fall through to SKIPPED

                    self._dispatch_tool_hook(
                        "on_permission_denied", action.name, action.args,
                        tool_result=None, permission_decision="ask",
                    )
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
                self._dispatch_tool_hook(
                    "on_before_tool_use", action.name, effective_args,
                    tool_result=None, permission_decision=permission.decision,
                )
                output = self._call_tool(
                    tool, action.name, effective_args, runtime_context=runtime_context
                )
                if output is None:
                    card = "\n".join(
                        [
                            "[TOOL_RESULT_MISSING]",
                            "",
                            f"Tool: `{action.name}`",
                            "Code: `TOOL_RESULT_MISSING`",
                            "",
                            "The tool returned no result. No success was inferred.",
                            "Retry the same call or choose another distinguishable action.",
                        ]
                    )
                    return self._finish_result(
                        action=action,
                        status=ActionStatus.ERROR,
                        start=start,
                        attempts=attempts,
                        tool_meta=tool_meta,
                        output=card,
                        error="Tool returned no result",
                        extra_metadata={
                            "error_category": "tool_result_missing",
                            "error_code": "TOOL_RESULT_MISSING",
                            "raw_tool_name": action.name,
                            "raw_arguments": dict(effective_args),
                            "recoverable": True,
                            "executed": True,
                        },
                    )
                self._dispatch_tool_hook(
                    "on_after_tool_use", action.name, effective_args,
                    tool_result=output, permission_decision=permission.decision,
                )
                # Track reads / invalidate writes for read-before-write
                self._track_file_access(action.name, effective_args, output)
                normalized_output = self._normalize_output(tool, output)
                latency = (time.monotonic() - start) * 1000
                result_metadata = {
                    **tool_meta,
                    "error_category": None,
                    "permission": self._permission_payload(permission),
                    "progress_count": len(runtime_context["progress_events"]),
                    "artifacts": list(runtime_context["artifacts"]),
                }
                if _auto_approved:
                    result_metadata["auto_approved"] = True
                    result_metadata["approval_required"] = True
                result = ActionResult(
                    name=action.name,
                    status=ActionStatus.SUCCESS,
                    output=normalized_output,
                    action_id=action.action_id,
                    attempts=attempts,
                    latency_ms=latency,
                    metadata=result_metadata,
                )
                # 6. Interceptor after_execute — can modify result
                if self._interceptor_chain is not None:
                    result = self._interceptor_chain.after_execute(action, result, interceptor_context)
                return result
            except Exception as exc:  # pragma: no cover - defensive path
                last_error = str(exc)
                # Check if this exception type is retryable
                if not isinstance(exc, _retryable_exceptions):
                    break
                # Exponential backoff with optional jitter
                if attempts < _max_attempts and _backoff_factor > 0:
                    import random
                    delay = min(_backoff_factor * (2 ** (attempts - 1)), _max_backoff)
                    if _jitter:
                        delay = delay * (0.5 + random.random())
                    time.sleep(delay)

        error_category = "runtime_error"
        if last_error and "not found" in last_error.lower():
            error_category = "tool_not_found"

        # Call on_failure callback if registered
        if _on_failure is not None:
            try:
                _on_failure(action=action, error=last_error, attempts=attempts)
            except Exception:
                pass  # on_failure must not raise

        error_result = self._finish_result(
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
        # Interceptor after_execute on error path too
        if self._interceptor_chain is not None:
            error_result = self._interceptor_chain.after_execute(action, error_result, interceptor_context)
        return error_result

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
        if output is None:
            code = str((extra_metadata or {}).get("error_code") or "TOOL_EXECUTION_ERROR")
            output = "\n".join(
                [
                    "[TOOL:error]",
                    "",
                    f"Tool: `{action.name}`",
                    f"Code: `{code}`",
                    "",
                    str(error or "The tool did not produce a result."),
                    "No success was inferred.",
                ]
            )
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
            "environment_attestation": dict(
                getattr(env, "attestation", {}) or {}
            ) if env is not None else {},
            "state": state,
            "ops": self._resolve_ops(required_ops, env),
            "tool_registry": self.tool_registry,
            "permission_context": permission_context,
            "progress_events": progress_events,
            "artifacts": artifacts,
            "emit_progress": _emit_progress,
            "record_artifact": _record_artifact,
            "delegate_depth": self.delegate_depth,
            "parent_run_id": "",
            "trace_writer": self.trace_writer,
            "shared_memory": self.shared_memory,
            "agent": getattr(self._engine, "agent", None) if self._engine is not None else None,
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
        # Use permission pipeline if available
        if self._pipeline is not None:
            tool_spec = getattr(tool, "spec", None) if tool is not None else None
            return self._pipeline.evaluate(
                tool_name=getattr(tool, "name", "") if tool else "",
                args=dict(args),
                tool_spec=tool_spec,
                runtime_context=runtime_context,
            )
        # Fallback: use tool's own permission check
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

    def _dispatch_tool_hook(
        self,
        hook_method: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_result: Any = None,
        permission_decision: Optional[str] = None,
    ) -> None:
        """Dispatch a tool-level hook to all registered engine hooks."""
        if self._engine is None:
            return
        hooks = getattr(self._engine, "hooks", None)
        if not hooks:
            return
        from .hooks import ToolHookContext
        # Native tool-calling executes through ActionExecutor instead of the
        # regular step-hook path.  The old placeholder ``0`` therefore made
        # every tool observation in a rollout appear to have happened at step
        # zero.  ``current_step`` is advanced only after a step has completed,
        # so while a tool is executing it is the authoritative step id.
        active_state = getattr(self._engine, "_active_state", None)
        step_id = int(getattr(active_state, "current_step", 0) or 0)
        ctx = ToolHookContext(
            task="",
            step_id=step_id,
            phase=RuntimePhase.ACT,
            state=active_state,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            permission_decision=permission_decision,
        )
        for hook in hooks:
            method = getattr(hook, hook_method, None)
            if method is not None:
                try:
                    method(ctx, self._engine)
                except Exception:
                    pass

    # ── Read-before-write support ──────────────────────────────────────────────

    _WRITE_TOOL_NAMES = frozenset({
        "file_edit_v2", "write_file", "Edit", "Write",
        "str_replace", "insert", "replace_lines", "append_file",
    })

    _READ_TOOL_NAMES = frozenset({
        "file_read_v2", "read_file", "Read", "view",
    })

    def _check_read_before_write(self, action: Action) -> Optional[ActionResult]:
        """Check read-before-write enforcement for file editing tools.

        Returns an ActionResult if the action should be blocked, None otherwise.
        """
        if self._rbw_enforcer is None:
            return None
        if action.name not in self._WRITE_TOOL_NAMES:
            return None

        path = action.args.get("path") or action.args.get("file_path", "")
        if not path:
            return None

        allowed, reason = self._rbw_enforcer.check_write(path)
        if allowed:
            return None

        start = time.monotonic()
        return self._finish_result(
            action=action,
            status=ActionStatus.SKIPPED,
            start=start,
            attempts=1,
            tool_meta=self._tool_meta(action.name),
            output={
                "status": "error",
                "message": reason,
                "error_category": "read_before_write",
            },
            extra_metadata={
                "error_category": "read_before_write",
            },
        )

    def _track_file_access(
        self, tool_name: str, args: Dict[str, Any], output: Any
    ) -> None:
        """Track file reads and invalidate cache on writes for RBW enforcement."""
        if self._rbw_enforcer is None:
            return

        # Record successful file reads
        if tool_name in self._READ_TOOL_NAMES:
            path = args.get("path") or args.get("file_path", "")
            if path and isinstance(output, dict):
                content = output.get("content", "")
                if content:
                    self._rbw_enforcer.record_read(path, content)
                elif isinstance(output, str) and output:
                    self._rbw_enforcer.record_read(path, output)

        # Invalidate cache after successful writes
        if tool_name in self._WRITE_TOOL_NAMES:
            path = args.get("path") or args.get("file_path", "")
            if path:
                self._rbw_enforcer.invalidate(path)
