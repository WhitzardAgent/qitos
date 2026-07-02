"""Tree-sitter parsing and AST-to-constraint-IR helpers for C and C++."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .constraint_ir import (
    And,
    BitmaskPredicate,
    BoolExpr,
    CallPredicate,
    Compare,
    IdentifierPredicate,
    Not,
    Or,
    UnknownPredicate,
    BinaryValue,
    CallValue,
    CastValue,
    DereferenceValue,
    IdentifierValue,
    LiteralValue,
    MemberValue,
    RawValue,
    SizeofValue,
    SubscriptValue,
    UnaryValue,
    ValueExpr,
    and_expr,
    or_expr,
)


_LANGUAGES: dict[str, Any] = {}
_LANGUAGE_ERROR: Optional[str] = None
_LANGUAGE_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpan:
    """Stable byte/line coordinates for one piece of source evidence."""

    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ParsedSource:
    source: bytes
    root: Any
    language: str
    has_error: bool
    error_count: int
    line_offset: int = 0
    transparent_boolean_macros: frozenset[str] = frozenset()
    noreturn_macros: frozenset[str] = frozenset()
    source_macros: frozenset[str] = frozenset()
    # Hold a reference to the Tree object so the C-owned root_node is not
    # freed by garbage collection while we still traverse it.
    _tree_ref: Any = None

    def text(self, node: Any) -> str:
        return self.source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def span(self, node: Any) -> SourceSpan:
        return SourceSpan(
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=self.line_offset + node.start_point.row + 1,
            start_column=node.start_point.column + 1,
            end_line=self.line_offset + node.end_point.row + 1,
            end_column=node.end_point.column + 1,
        )

    def local_error_count(self, node: Any) -> int:
        """Count parse damage in one evidence region, not the whole file."""
        if node is None:
            return self.error_count
        return sum(1 for item in walk(node) if item.type == "ERROR" or item.is_missing)

    def local_has_error(self, node: Any) -> bool:
        return bool(node is None or node.has_error or self.local_error_count(node))


def _load_languages() -> tuple[dict[str, Any], Optional[str]]:
    global _LANGUAGE_ERROR
    if _LANGUAGES or _LANGUAGE_ERROR:
        return _LANGUAGES, _LANGUAGE_ERROR
    with _LANGUAGE_LOCK:
        if _LANGUAGES or _LANGUAGE_ERROR:
            return _LANGUAGES, _LANGUAGE_ERROR
        try:
            import tree_sitter_c as tree_sitter_c
            import tree_sitter_cpp as tree_sitter_cpp
            from tree_sitter import Language

            _LANGUAGES.update({
                "c": Language(tree_sitter_c.language()),
                "cpp": Language(tree_sitter_cpp.language()),
            })
        except Exception as exc:  # Import and ABI failures are both optional-dependency failures.
            _LANGUAGE_ERROR = f"{type(exc).__name__}: {exc}"
    return _LANGUAGES, _LANGUAGE_ERROR


def tree_sitter_status() -> tuple[bool, Optional[str]]:
    languages, error = _load_languages()
    return bool(languages), error


def parse_source(
    source_text: str | bytes,
    *,
    file_extension: str = ".c",
    language: Optional[str] = None,
    line_offset: int = 0,
    preferred_symbols: Sequence[str] = (),
) -> Optional[ParsedSource]:
    """Parse C/C++ with an isolated parser; ambiguous headers try both grammars."""
    languages, _ = _load_languages()
    if not languages:
        return None
    source = source_text if isinstance(source_text, bytes) else source_text.encode("utf-8", errors="replace")

    requested = (language or "").strip().lower()
    if requested in {"c++", "cxx", "cc"}:
        requested = "cpp"
    if requested and requested not in languages:
        raise ValueError(f"unsupported language {language!r}; expected 'c' or 'cpp'")

    suffix = Path(file_extension or "").suffix.lower() or str(file_extension or "").lower()
    if requested:
        choices = [requested]
    elif suffix in {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}:
        choices = ["cpp"]
    elif suffix == ".h":
        choices = ["c", "cpp"]
    else:
        choices = ["c"]

    transparent_macros, noreturn_macros, source_macros = _source_macro_models(source)
    parsed: list[ParsedSource] = []
    for choice in choices:
        try:
            from tree_sitter import Parser

            parser = Parser(languages[choice])
            tree = parser.parse(source)
            error_count = sum(1 for node in walk(tree.root_node) if node.type == "ERROR" or node.is_missing)
            parsed.append(ParsedSource(
                source=source,
                root=tree.root_node,
                language=choice,
                has_error=bool(tree.root_node.has_error),
                error_count=error_count,
                line_offset=max(0, int(line_offset or 0)),
                transparent_boolean_macros=transparent_macros,
                noreturn_macros=noreturn_macros,
                source_macros=source_macros,
                _tree_ref=tree,
            ))
        except Exception as exc:
            _LOG.debug("Tree-sitter %s parse failed: %s", choice, exc)
            continue
    if not parsed:
        return None
    symbols = tuple(item for item in preferred_symbols if item)
    if symbols and len(parsed) > 1:
        def local_rank(item: ParsedSource) -> tuple[int, int, int, bool, int]:
            matches = [
                function for function in descendants(item.root, "function_definition")
                if any(function_matches(function_name(function, item), symbol) for symbol in symbols)
            ]
            local_errors = min((item.local_error_count(function) for function in matches), default=10**6)
            return (not bool(matches), local_errors, item.error_count, item.has_error, choices.index(item.language))
        return min(parsed, key=local_rank)
    return min(parsed, key=lambda item: (item.error_count, item.has_error, choices.index(item.language)))


def _source_macro_models(source: bytes) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Model only one-line macros whose semantics are syntactically provable."""
    text = source.decode("utf-8", errors="replace")
    transparent: set[str] = set()
    noreturn: set[str] = set()
    defined: set[str] = set()
    for match in re.finditer(
        r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*([^\r\n]+)$",
        text,
    ):
        name, parameter_text, body = match.groups()
        defined.add(name)
        parameters = [part.strip() for part in parameter_text.split(",") if part.strip()]
        compact = re.sub(r"\s+", "", body)
        if len(parameters) == 1:
            parameter = re.escape(parameters[0])
            identity = rf"\(*{parameter}\)*"
            double_not = rf"\(*!!\(*{parameter}\)*\)*"
            builtin = rf"__builtin_expect\((?:{identity}|{double_not}),[01]\)"
            if re.fullmatch(rf"(?:{identity}|{double_not}|{builtin})", compact):
                transparent.add(name)
        if re.search(r"\b(?:abort|exit|_Exit|quick_exit|__builtin_trap|terminate)\s*\(", body):
            noreturn.add(name)
    return frozenset(transparent), frozenset(noreturn), frozenset(defined)


def walk(node: Any) -> Iterable[Any]:
    """Non-recursive tree walk using an explicit stack to avoid C-level stack overflows."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        # Push children in reverse so leftmost is visited first
        for child in reversed(current.named_children):
            stack.append(child)


def descendants(node: Any, type_name: str) -> Iterable[Any]:
    return (item for item in walk(node) if item.type == type_name)


def contains(outer: Any, inner: Any) -> bool:
    return outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte


def unwrap_condition(node: Any) -> Any:
    """Remove grammar-specific condition/parenthesis wrappers."""
    current = node
    wrappers = {"parenthesized_expression", "condition_clause"}
    while current is not None and current.type in wrappers and len(current.named_children) == 1:
        current = current.named_children[0]
    return current


def callee_text(call_node: Any, parsed: ParsedSource) -> str:
    function = call_node.child_by_field_name("function")
    return parsed.text(function).strip() if function is not None else ""


def callee_leaf(call_node: Any, parsed: ParsedSource) -> str:
    function = call_node.child_by_field_name("function")
    if function is None:
        return ""
    field = function.child_by_field_name("field")
    if field is not None:
        return parsed.text(field).strip()
    identifiers = [
        node for node in walk(function)
        if node.type in {"identifier", "field_identifier", "operator_name", "destructor_name"}
    ]
    if identifiers:
        return parsed.text(identifiers[-1]).strip()
    text = parsed.text(function).strip()
    return text.rsplit("::", 1)[-1]


def call_matches(call_node: Any, target_function: str, parsed: ParsedSource) -> bool:
    target = target_function.strip()
    if not target:
        return False
    full = callee_text(call_node, parsed)
    leaf = callee_leaf(call_node, parsed)
    target_leaf = target.rsplit("::", 1)[-1].rsplit(".", 1)[-1].rsplit("->", 1)[-1]
    if "::" in target:
        return full.lstrip(":") == target.lstrip(":")
    return full == target or leaf == target or leaf == target_leaf


def function_name(function_node: Any, parsed: ParsedSource) -> str:
    declarator = function_node.child_by_field_name("declarator")
    current = declarator
    while current is not None:
        nested = current.child_by_field_name("declarator")
        if nested is None:
            break
        current = nested
    if current is not None:
        return parsed.text(current).strip()
    return ""


def enclosing_function(node: Any, parsed: ParsedSource) -> tuple[Optional[Any], str]:
    current = node.parent
    while current is not None:
        if current.type == "function_definition":
            return current, function_name(current, parsed)
        current = current.parent
    return None, ""


def function_matches(actual: str, requested: str) -> bool:
    if not requested:
        return True
    return actual == requested or actual.rsplit("::", 1)[-1] == requested.rsplit("::", 1)[-1]


def argument_texts(call_node: Any, parsed: ParsedSource) -> tuple[str, ...]:
    arguments = call_node.child_by_field_name("arguments")
    if arguments is None:
        return ()
    return tuple(parsed.text(child).strip() for child in arguments.named_children)


def value_to_ir(node: Any, parsed: ParsedSource) -> ValueExpr:
    """Convert a scalar C/C++ expression into typed value IR."""
    node = unwrap_condition(node)
    if node is None:
        return RawValue("", "missing")
    raw = parsed.text(node).strip()
    if node.type in {"identifier", "field_identifier", "qualified_identifier", "this"}:
        return IdentifierValue(raw, raw=raw)
    if node.type in {
        "number_literal",
        "char_literal",
        "string_literal",
        "true",
        "false",
        "null",
        "nullptr",
    }:
        return LiteralValue(raw, raw=raw)
    if node.type == "field_expression":
        base = node.child_by_field_name("argument") or node.child_by_field_name("object")
        field = node.child_by_field_name("field")
        operator = "->" if "->" in raw else "."
        if base is not None and field is not None:
            return MemberValue(value_to_ir(base, parsed), parsed.text(field).strip(), operator, raw=raw)
    if node.type == "subscript_expression":
        base = node.child_by_field_name("argument")
        index = node.child_by_field_name("index")
        if base is None and node.named_children:
            base = node.named_children[0]
        if index is None and len(node.named_children) > 1:
            index = node.named_children[1]
        if base is not None and index is not None:
            return SubscriptValue(value_to_ir(base, parsed), value_to_ir(index, parsed), raw=raw)
    if node.type == "call_expression":
        arguments = node.child_by_field_name("arguments")
        args = tuple(value_to_ir(item, parsed) for item in (arguments.named_children if arguments else ()))
        return CallValue(callee_text(node, parsed), args, raw=raw)
    if node.type == "cast_expression":
        type_node = node.child_by_field_name("type")
        value_node = node.child_by_field_name("value")
        if value_node is None and node.named_children:
            value_node = node.named_children[-1]
        if type_node is not None and value_node is not None:
            return CastValue(parsed.text(type_node).strip(), value_to_ir(value_node, parsed), raw=raw)
    if node.type in {"sizeof_expression", "sizeof_type_descriptor"}:
        operand = node.child_by_field_name("value")
        if operand is None and node.named_children:
            operand = node.named_children[-1]
        if operand is not None:
            return SizeofValue(value_to_ir(operand, parsed), raw=raw)
    if node.type in {"pointer_expression", "unary_expression", "update_expression"}:
        operator_node = node.child_by_field_name("operator")
        operator = parsed.text(operator_node).strip() if operator_node is not None else raw[:1]
        operand = node.child_by_field_name("argument")
        if operand is None and node.named_children:
            operand = node.named_children[-1]
        if operand is not None:
            if operator == "*":
                return DereferenceValue(value_to_ir(operand, parsed), raw=raw)
            return UnaryValue(operator, value_to_ir(operand, parsed), raw=raw)
    if node.type in {"binary_expression", "assignment_expression"}:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        operator_node = node.child_by_field_name("operator")
        operator = parsed.text(operator_node).strip() if operator_node is not None else ""
        if left is not None and right is not None:
            return BinaryValue(value_to_ir(left, parsed), operator, value_to_ir(right, parsed), raw=raw)
    return RawValue(raw, node.type)


def expression_to_ir(node: Any, parsed: ParsedSource) -> BoolExpr:
    """Recursively convert a Tree-sitter condition subtree into BoolExpr."""
    node = unwrap_condition(node)
    if node is None:
        return UnknownPredicate("", "missing")
    raw = parsed.text(node).strip()

    if node.type == "binary_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        operator_node = node.child_by_field_name("operator")
        operator = parsed.text(operator_node).strip() if operator_node is not None else ""
        if left is None or right is None:
            return UnknownPredicate(raw, node.type)
        if operator == "&&":
            return and_expr((expression_to_ir(left, parsed), expression_to_ir(right, parsed)), raw=raw)
        if operator == "||":
            return or_expr((expression_to_ir(left, parsed), expression_to_ir(right, parsed)), raw=raw)
        if operator in {"==", "!=", "<", "<=", ">", ">="}:
            left_unwrapped = unwrap_condition(left)
            if left_unwrapped is not None and left_unwrapped.type == "binary_expression":
                nested_op = left_unwrapped.child_by_field_name("operator")
                if nested_op is not None and parsed.text(nested_op).strip() == "&":
                    value = left_unwrapped.child_by_field_name("left")
                    mask = left_unwrapped.child_by_field_name("right")
                    if value is not None and mask is not None:
                        return BitmaskPredicate(
                            value_to_ir(value, parsed),
                            value_to_ir(mask, parsed),
                            operator,
                            value_to_ir(right, parsed),
                            raw=raw,
                        )
            return Compare(value_to_ir(left, parsed), operator, value_to_ir(right, parsed), raw=raw)
        if operator == "&":
            return BitmaskPredicate(value_to_ir(left, parsed), value_to_ir(right, parsed), raw=raw)
        return UnknownPredicate(raw, node.type)

    if node.type == "unary_expression":
        operator_node = node.child_by_field_name("operator")
        operator = parsed.text(operator_node).strip() if operator_node is not None else ""
        argument = node.child_by_field_name("argument")
        if argument is None and node.named_children:
            argument = node.named_children[-1]
        if operator == "!" and argument is not None:
            return Not(expression_to_ir(argument, parsed), raw=raw)
        return UnknownPredicate(raw, node.type)

    if node.type in {
        "identifier",
        "field_identifier",
        "qualified_identifier",
        "field_expression",
        "subscript_expression",
    }:
        return IdentifierPredicate(raw, raw=raw)

    if node.type == "call_expression":
        function = callee_text(node, parsed)
        args = argument_texts(node, parsed)
        arguments = node.child_by_field_name("arguments")
        argument_nodes = list(arguments.named_children) if arguments is not None else []
        if callee_leaf(node, parsed) in parsed.transparent_boolean_macros and len(argument_nodes) == 1:
            return expression_to_ir(argument_nodes[0], parsed)
        if callee_leaf(node, parsed) in parsed.source_macros:
            return UnknownPredicate(raw, "macro_call")
        if callee_leaf(node, parsed) in {"memcmp", "strcmp", "strncmp", "strcasecmp", "strncasecmp"}:
            return Compare(raw, "!=", "0", raw=raw)
        return CallPredicate(function, args, raw=raw)

    if node.type in {"true", "false", "nullptr", "null"}:
        return IdentifierPredicate(raw, raw=raw)

    # C++ wraps some expressions more deeply than C; preserve what we cannot prove.
    return UnknownPredicate(raw, node.type)


def target_span_matches(
    call_node: Any,
    parsed: ParsedSource,
    target_callsite: Any,
) -> bool:
    """Accept a SourceSpan, mapping, line number, or (start, end) line tuple."""
    if target_callsite is None:
        return True
    span = parsed.span(call_node)
    if isinstance(target_callsite, SourceSpan):
        return span.start_byte == target_callsite.start_byte and span.end_byte == target_callsite.end_byte
    if isinstance(target_callsite, int):
        return span.start_line <= target_callsite <= span.end_line
    if isinstance(target_callsite, Sequence) and not isinstance(target_callsite, (str, bytes)):
        values = list(target_callsite)
        if len(values) >= 2:
            return span.start_line == int(values[0]) and span.end_line == int(values[1])
    if isinstance(target_callsite, Mapping):
        if "start_byte" in target_callsite:
            return (
                span.start_byte == int(target_callsite["start_byte"])
                and span.end_byte == int(target_callsite.get("end_byte", span.end_byte))
            )
        line = target_callsite.get("line", target_callsite.get("start_line"))
        if line is not None:
            end_line = int(target_callsite.get("end_line", line))
            return span.start_line == int(line) and span.end_line == end_line
    return False
