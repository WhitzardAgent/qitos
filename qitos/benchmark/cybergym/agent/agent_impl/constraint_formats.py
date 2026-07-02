"""Semantic evidence for format-comparison predicates inside control conditions."""

from __future__ import annotations

import ast
import re
from typing import Any, Optional

from .constraint_ast import ParsedSource, argument_texts, callee_leaf, descendants
from .constraint_ir import BoolExpr, Compare, Not, render_value, walk_expr


FORMAT_FUNCTIONS = frozenset({
    "memcmp",
    "strcmp",
    "strncmp",
    "strcasecmp",
    "strncasecmp",
})


def _comparison_relation(expr: BoolExpr, call_text: str) -> str:
    """Describe the normalized relation applied to one format call."""
    for item in walk_expr(expr):
        if (
            isinstance(item, Compare)
            and render_value(item.left).strip() == call_text.strip()
            and render_value(item.right).strip() == "0"
        ):
            return "equal" if item.operator == "==" else "not_equal" if item.operator == "!=" else item.operator
        if isinstance(item, Not) and getattr(item.operand, "call_text", "") == call_text:
            return "equal"
    return "truthy_or_compound"


def _decode_c_string(raw_literal: str) -> Optional[str]:
    """Best-effort decode while retaining raw_literal as the authoritative evidence."""
    literal = raw_literal.strip()
    # Strip common C/C++ prefixes for Python's literal parser.
    literal = re.sub(r"^(?:u8|u|U|L)(?=[\"'])", "", literal)
    try:
        value = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str):
        return value.encode("latin-1", errors="backslashreplace").hex()
    return None


def _buffer_parts(expression: str) -> tuple[str, str]:
    """Extract the common ``base + offset`` shape without claiming alias analysis."""
    match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*\+\s*(.+?)\s*", expression)
    if not match:
        return expression.strip(), ""
    return match.group(1), match.group(2).strip()


def extract_format_details(
    condition_node: Any,
    normalized_expr: BoolExpr,
    parsed: ParsedSource,
) -> list[dict[str, Any]]:
    """Inspect calls in the condition only and retain comparison/literal evidence."""
    details: list[dict[str, Any]] = []
    for call in descendants(condition_node, "call_expression"):
        function = callee_leaf(call, parsed)
        if function not in FORMAT_FUNCTIONS:
            continue
        args = argument_texts(call, parsed)
        if len(args) < 2:
            continue
        call_text = parsed.text(call).strip()
        buffer_expression = args[0]
        base_expression, offset_expression = _buffer_parts(buffer_expression)
        raw_literal = args[1]
        literal_is_string = bool(re.match(r"^(?:u8|u|U|L)?\"", raw_literal.strip()))
        length_expression = args[2] if function in {"memcmp", "strncmp", "strncasecmp"} and len(args) > 2 else ""
        details.append({
            "function": function,
            "call_text": call_text,
            "buffer_expression": buffer_expression,
            "base_expression": base_expression,
            "offset_expression": offset_expression,
            "raw_literal": raw_literal if literal_is_string else "",
            "expected_hex": _decode_c_string(raw_literal) if literal_is_string else None,
            "length_expression": length_expression,
            "comparison_scope": "prefix" if length_expression else "full_c_string",
            "relation": _comparison_relation(normalized_expr, call_text),
        })
    return details
