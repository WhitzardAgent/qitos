"""Tool abstraction and decorator for QitOS kernel."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, cast


@dataclass
class ToolPermission:
    filesystem_read: bool = False
    filesystem_write: bool = False
    network: bool = False
    command: bool = False


@dataclass
class ToolValidationResult:
    valid: bool = True
    message: str = ""
    code: str = ""
    suggested_args: Optional[Dict[str, Any]] = None

    @classmethod
    def ok(cls) -> "ToolValidationResult":
        return cls(valid=True)

    @classmethod
    def fail(
        cls,
        message: str,
        *,
        code: str = "validation_failed",
        suggested_args: Optional[Dict[str, Any]] = None,
    ) -> "ToolValidationResult":
        return cls(
            valid=False, message=message, code=code, suggested_args=suggested_args
        )


@dataclass
class ToolPermissionRule:
    effect: str  # allow | deny | ask
    tool_name: str = ""
    tool_family: str = ""
    scope: str = ""
    message: str = ""

    def matches(self, tool_name: str, scope: str = "") -> bool:
        normalized_tool = str(tool_name or "")
        normalized_scope = str(scope or "")
        if self.tool_name and self.tool_name != normalized_tool:
            return False
        if self.tool_family and not (
            normalized_tool == self.tool_family
            or normalized_tool.startswith(f"{self.tool_family}.")
        ):
            return False
        if self.scope and self.scope != normalized_scope:
            return False
        return bool(self.tool_name or self.tool_family or self.scope)


@dataclass
class ToolPermissionDecision:
    decision: str  # allow | deny | ask
    message: str = ""
    scope: str = ""
    matched_rule: Optional[ToolPermissionRule] = None
    updated_args: Optional[Dict[str, Any]] = None

    @classmethod
    def allow(
        cls, *, scope: str = "", updated_args: Optional[Dict[str, Any]] = None
    ) -> "ToolPermissionDecision":
        return cls(decision="allow", scope=scope, updated_args=updated_args)

    @classmethod
    def deny(
        cls,
        message: str,
        *,
        scope: str = "",
        matched_rule: Optional[ToolPermissionRule] = None,
    ) -> "ToolPermissionDecision":
        return cls(
            decision="deny", message=message, scope=scope, matched_rule=matched_rule
        )

    @classmethod
    def ask(
        cls,
        message: str,
        *,
        scope: str = "",
        matched_rule: Optional[ToolPermissionRule] = None,
        updated_args: Optional[Dict[str, Any]] = None,
    ) -> "ToolPermissionDecision":
        return cls(
            decision="ask",
            message=message,
            scope=scope,
            matched_rule=matched_rule,
            updated_args=updated_args,
        )


@dataclass
class ToolPermissionContext:
    allow_rules: List[ToolPermissionRule] = field(default_factory=list)
    deny_rules: List[ToolPermissionRule] = field(default_factory=list)
    ask_rules: List[ToolPermissionRule] = field(default_factory=list)
    default_decision: str = "allow"

    def evaluate(self, tool_name: str, scope: str = "") -> ToolPermissionDecision:
        for rule in self.deny_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.deny(
                    rule.message or f"Tool '{tool_name}' is denied.",
                    scope=scope,
                    matched_rule=rule,
                )
        for rule in self.ask_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.ask(
                    rule.message or f"Tool '{tool_name}' requires user confirmation.",
                    scope=scope,
                    matched_rule=rule,
                )
        for rule in self.allow_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.allow(scope=scope)
        if self.default_decision == "deny":
            return ToolPermissionDecision.deny(
                f"Tool '{tool_name}' is denied by the default permission policy.",
                scope=scope,
            )
        if self.default_decision == "ask":
            return ToolPermissionDecision.ask(
                f"Tool '{tool_name}' requires confirmation by the default permission policy.",
                scope=scope,
            )
        return ToolPermissionDecision.allow(scope=scope)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolPermissionContext":
        def _rules(items: Any) -> List[ToolPermissionRule]:
            rules: List[ToolPermissionRule] = []
            for item in list(items or []):
                if isinstance(item, ToolPermissionRule):
                    rules.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                rules.append(
                    ToolPermissionRule(
                        effect=str(item.get("effect", "")),
                        tool_name=str(item.get("tool_name", "")),
                        tool_family=str(item.get("tool_family", "")),
                        scope=str(item.get("scope", "")),
                        message=str(item.get("message", "")),
                    )
                )
            return rules

        return cls(
            allow_rules=_rules(payload.get("allow_rules")),
            deny_rules=_rules(payload.get("deny_rules")),
            ask_rules=_rules(payload.get("ask_rules")),
            default_decision=str(payload.get("default_decision", "allow")),
        )


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    timeout_s: Optional[float] = None
    max_retries: int = 0
    permissions: ToolPermission = field(default_factory=ToolPermission)
    required_ops: List[str] = field(default_factory=list)
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    read_only: bool = False
    concurrency_safe: bool = False
    requires_user_interaction: bool = False
    supports_background: bool = False
    result_max_chars: Optional[int] = None
    produces_artifact: bool = False
    rule_scope_builder: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None
    prompt: str = ""


@dataclass
class ToolMeta:
    name: Optional[str] = None
    description: Optional[str] = None
    prompt: str = ""
    timeout_s: Optional[float] = None
    max_retries: int = 0
    permissions: ToolPermission = field(default_factory=ToolPermission)
    required_ops: List[str] = field(default_factory=list)
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    read_only: bool = False
    concurrency_safe: bool = False
    requires_user_interaction: bool = False
    supports_background: bool = False
    result_max_chars: Optional[int] = None
    produces_artifact: bool = False
    rule_scope_builder: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None


class BaseTool:
    """Base abstraction for callable tools."""

    def __init__(self, spec: ToolSpec):
        description = (
            inspect.getdoc(self.execute)
            or inspect.getdoc(self.run)
            or inspect.getdoc(self.__class__)
        )
        if description:
            spec.description = inspect.cleandoc(description)
        if spec.input_schema is None:
            spec.input_schema = {
                "type": "object",
                "properties": dict(spec.parameters),
                "required": list(spec.required),
            }
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def _coerce_run_kwargs(
        self, args: tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not args:
            return dict(kwargs)
        param_names = list(self.spec.parameters.keys())
        if len(args) > len(param_names):
            raise TypeError(
                f"{self.__class__.__name__}.run() received too many positional arguments"
            )
        merged = dict(kwargs)
        for name, value in zip(param_names, args):
            if name in merged:
                raise TypeError(
                    f"{self.__class__.__name__}.run() got multiple values for argument '{name}'"
                )
            merged[name] = value
        return merged

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Compatibility wrapper that routes legacy run calls through `execute(...)`."""
        runtime_context = kwargs.pop("runtime_context", None)
        coerced = self._coerce_run_kwargs(args, kwargs)
        return self.execute(coerced, runtime_context=runtime_context)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Normalized call path for tool execution."""
        return self.execute(args, runtime_context=runtime_context)

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        _ = args
        _ = runtime_context
        return ToolValidationResult.ok()

    def check_permissions(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionDecision:
        runtime_context = runtime_context or {}
        context = runtime_context.get("permission_context")
        if isinstance(context, dict):
            context = ToolPermissionContext.from_dict(context)
        if not isinstance(context, ToolPermissionContext):
            return ToolPermissionDecision.allow(scope=self.build_rule_scope(args))
        return context.evaluate(self.name, self.build_rule_scope(args))

    def build_rule_scope(self, args: Dict[str, Any]) -> str:
        builder = getattr(self.spec, "rule_scope_builder", None)
        if callable(builder):
            value = builder(dict(args))
            return str(value or "")
        return ""

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute tool with optional runtime context."""
        legacy_run = type(self).run
        if legacy_run is not BaseTool.run:
            call_kwargs = dict(args)
            run_sig = inspect.signature(legacy_run)
            if "runtime_context" in run_sig.parameters:
                call_kwargs["runtime_context"] = runtime_context
            return legacy_run(self, **call_kwargs)
        raise NotImplementedError

    def __call__(self, **kwargs: Any) -> Any:
        return self.run(**kwargs)


class FunctionTool(BaseTool):
    """Tool wrapper around callable functions or bound methods."""

    def __init__(self, func: Callable[..., Any], meta: Optional[ToolMeta] = None):
        self.func = func
        self.meta = meta or get_tool_meta(func) or ToolMeta()
        spec = build_tool_spec(func, self.meta)
        super().__init__(spec)
        description = inspect.getdoc(func) or self.meta.description
        if description:
            self.spec.description = inspect.cleandoc(description)

    def run(self, **kwargs: Any) -> Any:
        return self.func(**kwargs)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self.execute(args, runtime_context=runtime_context)

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        runtime_context = runtime_context or {}
        env = runtime_context.get("env")
        ops = runtime_context.get("ops", {})
        sig = inspect.signature(self.func)
        call_kwargs = dict(args)
        if "runtime_context" in sig.parameters:
            call_kwargs["runtime_context"] = runtime_context
        if "env" in sig.parameters:
            call_kwargs["env"] = env
        if "ops" in sig.parameters:
            call_kwargs["ops"] = ops
        if "file_ops" in sig.parameters and "file" in ops:
            call_kwargs["file_ops"] = ops["file"]
        if "process_ops" in sig.parameters and "process" in ops:
            call_kwargs["process_ops"] = ops["process"]
        return self.func(**call_kwargs)


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    prompt: str = "",
    timeout_s: Optional[float] = None,
    max_retries: int = 0,
    permissions: Optional[ToolPermission] = None,
    required_ops: Optional[List[str]] = None,
    input_schema: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    read_only: bool = False,
    concurrency_safe: bool = False,
    requires_user_interaction: bool = False,
    supports_background: bool = False,
    result_max_chars: Optional[int] = None,
    produces_artifact: bool = False,
    rule_scope_builder: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
):
    """Decorator that marks a callable as a QitOS tool without changing binding semantics."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        meta = ToolMeta(
            name=name,
            description=description,
            prompt=prompt,
            timeout_s=timeout_s,
            max_retries=max_retries,
            permissions=permissions or ToolPermission(),
            required_ops=list(required_ops or []),
            input_schema=input_schema,
            output_schema=output_schema,
            read_only=read_only,
            concurrency_safe=concurrency_safe,
            requires_user_interaction=requires_user_interaction,
            supports_background=supports_background,
            result_max_chars=result_max_chars,
            produces_artifact=produces_artifact,
            rule_scope_builder=rule_scope_builder,
        )
        setattr(func, "__qitos_tool_meta__", meta)
        setattr(func, "_is_tool", True)
        return func

    return decorator


def get_tool_meta(func: Callable[..., Any]) -> Optional[ToolMeta]:
    if hasattr(func, "__qitos_tool_meta__"):
        return getattr(func, "__qitos_tool_meta__")

    underlying = getattr(func, "__func__", None)
    if underlying is not None and hasattr(underlying, "__qitos_tool_meta__"):
        return getattr(underlying, "__qitos_tool_meta__")

    return None


def build_tool_spec(func: Callable[..., Any], meta: ToolMeta) -> ToolSpec:
    sig = inspect.signature(func)
    params = {}
    required = []

    for name, p in sig.parameters.items():
        if name in {
            "self",
            "cls",
            "runtime_context",
            "env",
            "ops",
            "file_ops",
            "process_ops",
        }:
            continue
        params[name] = {"type": _type_to_json(p.annotation), "description": ""}
        if p.default is inspect.Parameter.empty:
            required.append(name)

    desc = inspect.getdoc(func) or meta.description or ""
    tool_name = str(meta.name or getattr(func, "__name__", "tool") or "tool")

    return ToolSpec(
        name=cast(str, tool_name),
        description=inspect.cleandoc(desc) if desc else "",
        parameters=params,
        required=required,
        timeout_s=meta.timeout_s,
        max_retries=meta.max_retries,
        permissions=meta.permissions,
        required_ops=list(meta.required_ops),
        input_schema=meta.input_schema
        or {
            "type": "object",
            "properties": params,
            "required": required,
        },
        output_schema=meta.output_schema,
        read_only=meta.read_only,
        concurrency_safe=meta.concurrency_safe,
        requires_user_interaction=meta.requires_user_interaction,
        supports_background=meta.supports_background,
        result_max_chars=meta.result_max_chars,
        produces_artifact=meta.produces_artifact,
        rule_scope_builder=meta.rule_scope_builder,
        prompt=meta.prompt,
    )


def _type_to_json(annotation: Any) -> str:
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array",
    }
    return mapping.get(annotation, "any")


__all__ = [
    "BaseTool",
    "FunctionTool",
    "ToolMeta",
    "ToolPermission",
    "ToolPermissionContext",
    "ToolPermissionDecision",
    "ToolPermissionRule",
    "ToolSpec",
    "ToolValidationResult",
    "build_tool_spec",
    "get_tool_meta",
    "tool",
]
