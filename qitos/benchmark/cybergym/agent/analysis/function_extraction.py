"""Function-definition and call-site extraction for C and C++.

Absorbed from tree-sitter-analyzer's function_extraction.py, trimmed to
C/C++ only and adapted for cybergym_agent's import conventions.
"""

from __future__ import annotations

from typing import Any, Optional, cast

# ---------------------------------------------------------------------------
# Node types — C/C++ only
# ---------------------------------------------------------------------------

_CALL_NODE_TYPES: dict[str, set[str]] = {
    "c": {"call_expression"},
    "cpp": {"call_expression"},
}

_FUNC_DEF_TYPES: dict[str, set[str]] = {
    "c": {"function_definition"},
    "cpp": {"function_definition"},
}

_IDENT_TYPES_C = ("identifier", "field_identifier", "destructor_name")

# ---------------------------------------------------------------------------
# Function name extraction — C/C++
# ---------------------------------------------------------------------------


def _declarator_name(declarator_node: Any) -> str | None:
    """Find the first identifier inside a ``function_declarator`` node."""
    for sub in declarator_node.children:
        if sub.type in ("identifier", "field_identifier"):
            return _node_text_value(sub)
    return None


def _func_name_c(node: Any) -> str | None:
    """C / C++: direct identifier types, or recurse into function_declarator."""
    for child in node.children:
        if child.type in _IDENT_TYPES_C:
            return _node_text_value(child)
        if child.type == "function_declarator":
            result = _declarator_name(child)
            if result:
                return result
    # Deeper declarator nesting (e.g. pointer to function)
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        current = declarator
        while current is not None:
            nested = current.child_by_field_name("declarator")
            if nested is None:
                break
            current = nested
        if current is not None:
            # The innermost declarator should contain the name
            for child in current.children:
                if child.type in _IDENT_TYPES_C:
                    return _node_text_value(child)
    return None


# ---------------------------------------------------------------------------
# Call info extraction — C/C++
# ---------------------------------------------------------------------------


def _call_info_c(node: Any, source: str, line_table: Any = None) -> dict[str, Any] | None:
    """C / C++: prefer function field, fall back to first identifier child."""
    func_node = node.child_by_field_name("function")
    if func_node is not None:
        name = _node_text(func_node, source)
        if line_table is not None:
            line, col = line_table.line_col(node.start_byte)
        else:
            line = node.start_point[0] + 1
            col = node.start_point[1]
        return {
            "name": name,
            "full_name": name,
            "line": line,
            "col": col,
            "receiver": None,
        }
    for child in node.children:
        if child.type == "identifier":
            return _call_from_text(_node_text(child, source), node, line_table)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def walk_tree(
    node: Any, source: str, language: str, line_table: Any = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk an AST and return function definitions plus call sites."""
    definitions: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    _extract_recursive(node, source, language, definitions, calls, None, None, line_table)
    return definitions, calls


def _extract_recursive(
    node: Any,
    source: str,
    language: str,
    definitions: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    enclosing_class: str | None,
    local_types: dict[str, tuple[str, int]] | None,
    line_table: Any = None,
) -> None:
    if not hasattr(node, "type"):
        return

    node_type = node.type

    if node_type in _FUNC_DEF_TYPES.get(language, set()):
        func_name = get_func_name(node, language)
        if func_name:
            if line_table is not None:
                sl, sc = line_table.line_col(node.start_byte)
                el, ec = line_table.line_col(node.end_byte)
            else:
                sl = node.start_point[0] + 1
                sc = node.start_point[1]
                el = node.end_point[0] + 1
                ec = node.end_point[1]
            definitions.append(
                {
                    "name": func_name,
                    "start_line": sl,
                    "start_col": sc,
                    "end_line": el,
                    "end_col": ec,
                    "class": enclosing_class,
                }
            )
            # C/C++: no local type inference needed; just descend.
            for child in node.children:
                _extract_recursive(
                    child,
                    source,
                    language,
                    definitions,
                    calls,
                    enclosing_class,
                    local_types,
                    line_table,
                )
            return

    if node_type in _CALL_NODE_TYPES.get(language, set()):
        call_info = extract_call(node, source, language, line_table)
        if call_info:
            calls.append(call_info)

    for child in node.children:
        _extract_recursive(
            child,
            source,
            language,
            definitions,
            calls,
            enclosing_class,
            local_types,
            line_table,
        )


def get_func_name(node: Any, language: str) -> str | None:
    """Extract a function name from a C/C++ definition node."""
    if language not in ("c", "cpp"):
        return None
    try:
        return cast("str | None", _func_name_c(node))
    except Exception:
        return None


def extract_call(node: Any, source: str, language: str, line_table: Any = None) -> dict[str, Any] | None:
    """Extract call target info from a C/C++ call node."""
    if language not in ("c", "cpp"):
        return None
    try:
        return cast("dict[str, Any] | None", _call_info_c(node, source, line_table))
    except Exception:
        return None


def find_parent_class_cpp(node: Any) -> str | None:
    """Walk up from a C++ function node to find an enclosing class/struct."""
    if node is None:
        return None
    current = node.parent
    while current is not None:
        if current.type in ("class_specifier", "struct_specifier"):
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                return _node_text_value(name_node)
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_from_text(text: str, node: Any, line_table: Any = None) -> dict[str, Any]:
    receiver = None
    name = text
    if "." in name:
        receiver, name = name.rsplit(".", 1)
    elif "->" in name:
        receiver, name = name.rsplit("->", 1)
    if line_table is not None:
        line, col = line_table.line_col(node.start_byte)
    else:
        line = node.start_point[0] + 1
        col = node.start_point[1]
    return {
        "name": name,
        "full_name": text,
        "line": line,
        "col": col,
        "receiver": receiver,
    }


def node_text(node: Any, source: str) -> str:
    """Extract text from a node using UTF-8 byte offsets safely."""
    return _node_text(node, source)


def _node_text(node: Any, source: str) -> str:
    if node is None:
        return ""
    text_attr = getattr(node, "text", None)
    if isinstance(text_attr, bytes):
        try:
            return text_attr.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            return ""
    if isinstance(text_attr, str):
        return text_attr
    try:
        return source.encode("utf-8")[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )
    except (IndexError, TypeError, UnicodeDecodeError):
        return ""


def _node_text_value(node: Any) -> str:
    text = node.text
    return text.decode("utf-8") if isinstance(text, bytes) else str(text)
