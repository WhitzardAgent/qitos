"""@function_tool decorator for QitOS — creates a FunctionTool from a plain function."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .tool import FunctionTool, ToolMeta, RetryPolicy
from .tool_schema import function_schema


def function_tool(
    func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    timeout_s: Optional[float] = None,
    max_retries: int = 0,
    retry_policy: Optional[RetryPolicy] = None,
    on_failure: Optional[Callable] = None,
    read_only: bool = False,
    concurrency_safe: bool = False,
    needs_approval: bool = False,
    **extra_meta: Any,
) -> Any:
    """Decorator that creates a :class:`FunctionTool` from a plain function.

    Can be used with or without parentheses::

        @function_tool
        def greet(name: str) -> str: ...

        @function_tool(name="custom", needs_approval=True)
        def greet(name: str) -> str: ...

    Returns a :class:`FunctionTool` instance.
    """

    def _make_tool(fn: Callable[..., Any]) -> FunctionTool:
        meta = ToolMeta(
            name=name,
            description=description,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_policy=retry_policy,
            on_failure=on_failure,
            read_only=read_only,
            concurrency_safe=concurrency_safe,
            needs_approval=needs_approval,
        )
        # Store extra_meta as attributes on meta for future extensibility
        for key, value in extra_meta.items():
            setattr(meta, key, value)

        # Build spec using enhanced schema from tool_schema
        import inspect

        from .tool import build_tool_spec

        schema_info = function_schema(fn)
        spec = build_tool_spec(fn, meta)

        # Override parameters with enriched schema from tool_schema
        for param_name, param_schema in schema_info["parameters"].items():
            if param_name in spec.parameters:
                # Merge: keep existing keys but enrich with description and richer types
                merged = dict(spec.parameters[param_name])
                # If tool_schema produced a richer type (dict with keys), replace the simple "type" string
                for k, v in param_schema.items():
                    if k == "type" and isinstance(v, str) and merged.get("type") == "any":
                        merged[k] = v
                    elif k != "type":
                        merged[k] = v
                    elif k == "type" and isinstance(v, str) and merged.get("type") != v and v != "any":
                        merged[k] = v
                # If tool_schema produced a dict type (e.g. with nullable, items, enum), merge those
                if isinstance(param_schema, dict):
                    for k, v in param_schema.items():
                        if k not in ("description", "default") and not (k == "type" and isinstance(v, str)):
                            merged[k] = v
                spec.parameters[param_name] = merged

        # Update the input_schema as well
        if spec.input_schema is not None:
            spec.input_schema = {
                "type": "object",
                "properties": dict(spec.parameters),
                "required": spec.required,
            }

        tool_instance = FunctionTool.__new__(FunctionTool)
        tool_instance.func = fn
        tool_instance.meta = meta
        tool_instance.spec = spec
        # Description: explicit meta.description overrides docstring
        if meta.description:
            spec.description = inspect.cleandoc(meta.description)
        else:
            desc = inspect.getdoc(fn) or ""
            if desc:
                spec.description = inspect.cleandoc(desc)

        return tool_instance

    if func is not None:
        # Used as @function_tool without parentheses
        return _make_tool(func)

    # Used as @function_tool(...) with parentheses
    return _make_tool
