"""Automatic schema generation from function signatures for QitOS tools."""

from __future__ import annotations

import inspect
import re
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    get_args,
    get_origin,
    get_type_hints,
)


def function_schema(func: Any) -> Dict[str, Any]:
    """Extract parameter names, type annotations, and defaults from a function signature.

    Returns a dict with keys:
      - ``parameters``: mapping of param name -> {"type": <json schema>, "description": "", "default": ...}
      - ``required``: list of parameter names without defaults
      - ``descriptions``: mapping of param name -> description from docstring
    """
    sig = inspect.signature(func)
    hints = {}
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:
        pass

    docstring = inspect.getdoc(func) or ""
    descriptions = parse_docstring(docstring)

    skip = {"self", "cls", "runtime_context", "env", "ops", "file_ops", "process_ops"}
    parameters: Dict[str, Dict[str, Any]] = {}
    required: List[str] = []

    for name, p in sig.parameters.items():
        if name in skip:
            continue
        annotation = hints.get(name, p.annotation)
        schema = type_to_json_schema(annotation)
        entry: Dict[str, Any] = dict(schema)
        entry["description"] = descriptions.get(name, "")
        if p.default is not inspect.Parameter.empty:
            entry["default"] = p.default
        else:
            required.append(name)
        parameters[name] = entry

    return {
        "parameters": parameters,
        "required": required,
        "descriptions": descriptions,
    }


def parse_docstring(docstring: str) -> Dict[str, str]:
    """Parse a Google-style docstring and extract Args descriptions.

    Supports the format::

        Args:
            x: The x value
            y: The y value

    Returns a dict mapping parameter name -> description string.
    """
    if not docstring:
        return {}

    # Strip common leading indentation (like inspect.cleandoc)
    docstring = inspect.cleandoc(docstring)

    result: Dict[str, str] = {}

    # Find the Args section
    match = re.search(r"^Args:\s*\n", docstring, re.MULTILINE)
    if not match:
        return result

    args_start = match.end()
    # Find the next section (e.g. Returns:, Raises:, or end of docstring)
    next_section = re.search(r"^\w+:\s*\n", docstring[args_start:], re.MULTILINE)
    args_block = docstring[args_start: args_start + next_section.start()] if next_section else docstring[args_start:]

    # Parse each parameter line — supports:
    #   name: description
    #   name (type): description
    #   name: multi-line description
    current_name: Optional[str] = None
    current_desc_lines: List[str] = []

    for line in args_block.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current_name is not None:
                current_desc_lines.append("")
            continue

        # Check if this is a new parameter line
        param_match = re.match(r"^(\w+)(?:\s*\([^)]*\))?\s*:\s*(.*)", stripped)
        if param_match:
            # Save previous param
            if current_name is not None:
                desc = " ".join(current_desc_lines).strip()
                # Collapse multiple spaces
                desc = re.sub(r"\s+", " ", desc)
                result[current_name] = desc
            current_name = param_match.group(1)
            current_desc_lines = [param_match.group(2)] if param_match.group(2) else []
        elif current_name is not None:
            # Continuation of previous description
            current_desc_lines.append(stripped)

    # Save last param
    if current_name is not None:
        desc = " ".join(current_desc_lines).strip()
        desc = re.sub(r"\s+", " ", desc)
        result[current_name] = desc

    return result


def type_to_json_schema(annotation: Any) -> Dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict.

    Supported types:
      - Basic: str, int, float, bool -> {type: string|integer|number|boolean}
      - Optional[X] -> {type: X, nullable: true}
      - list[X] -> {type: array, items: X}
      - dict[K,V] -> {type: object}
      - Literal[...] -> {type: X, enum: [...]}
      - Annotated[type, ...] -> transparent passthrough to inner type
      - Fallback: {}
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return {}

    # Unwrap Annotated — transparent passthrough to inner type
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return type_to_json_schema(args[0])

    # Handle Optional[X] — Union[X, None]
    if origin is Optional:
        args = get_args(annotation)
        if args:
            inner = type_to_json_schema(args[0])
            result = dict(inner)
            result["nullable"] = True
            return result

    # Handle Union[X, None] explicitly (same as Optional but written differently)
    import typing
    if origin is getattr(typing, "Union", None):
        args = get_args(annotation)
        if args:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                inner = type_to_json_schema(non_none[0])
                result = dict(inner)
                result["nullable"] = True
                return result

    # Handle Literal[...]
    if origin is Literal:
        args = get_args(annotation)
        if not args:
            return {}
        # Infer type from first value
        first = args[0]
        if isinstance(first, bool):
            base_type = "boolean"
        elif isinstance(first, int):
            base_type = "integer"
        elif isinstance(first, float):
            base_type = "number"
        elif isinstance(first, str):
            base_type = "string"
        else:
            base_type = "string"
        return {"type": base_type, "enum": list(args)}

    # Handle list[X]
    if origin is list:
        args = get_args(annotation)
        if args:
            return {"type": "array", "items": type_to_json_schema(args[0])}
        return {"type": "array"}

    # Handle dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Basic types
    basic: Dict[type, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    if annotation in basic:
        return {"type": basic[annotation]}

    # Fallback for bare dict/list without parameters
    if annotation is dict:
        return {"type": "object"}
    if annotation is list:
        return {"type": "array"}

    return {}
