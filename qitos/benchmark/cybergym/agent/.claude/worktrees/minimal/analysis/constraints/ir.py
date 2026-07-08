"""Small boolean-expression IR used by target-relative constraint extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Tuple, Union


class ValueExpr:
    """Base class for typed scalar expressions used by predicates."""

    raw: str

    def render(self) -> str:
        return render_value(self)


@dataclass(frozen=True)
class RawValue(ValueExpr):
    raw: str
    node_type: str = "unknown"


@dataclass(frozen=True)
class IdentifierValue(ValueExpr):
    name: str
    raw: str = ""


@dataclass(frozen=True)
class LiteralValue(ValueExpr):
    value: str
    raw: str = ""


@dataclass(frozen=True)
class MemberValue(ValueExpr):
    base: "ValueLike"
    member: str
    operator: str = "."
    raw: str = ""


@dataclass(frozen=True)
class SubscriptValue(ValueExpr):
    base: "ValueLike"
    index: "ValueLike"
    raw: str = ""


@dataclass(frozen=True)
class DereferenceValue(ValueExpr):
    operand: "ValueLike"
    raw: str = ""


@dataclass(frozen=True)
class CallValue(ValueExpr):
    function: str
    arguments: Tuple["ValueLike", ...]
    raw: str = ""


@dataclass(frozen=True)
class CastValue(ValueExpr):
    type_name: str
    operand: "ValueLike"
    raw: str = ""


@dataclass(frozen=True)
class SizeofValue(ValueExpr):
    operand: "ValueLike"
    raw: str = ""


@dataclass(frozen=True)
class UnaryValue(ValueExpr):
    operator: str
    operand: "ValueLike"
    raw: str = ""


@dataclass(frozen=True)
class BinaryValue(ValueExpr):
    left: "ValueLike"
    operator: str
    right: "ValueLike"
    raw: str = ""


ValueLike = Union[str, ValueExpr]


class BoolExpr:
    """Base class for a condition that must hold to reach a target callsite."""

    raw: str

    def render(self) -> str:
        return _render(self)

    def negate(self) -> "BoolExpr":
        return negate(self)


@dataclass(frozen=True)
class And(BoolExpr):
    terms: Tuple[BoolExpr, ...]
    raw: str = ""


@dataclass(frozen=True)
class Or(BoolExpr):
    terms: Tuple[BoolExpr, ...]
    raw: str = ""


@dataclass(frozen=True)
class Not(BoolExpr):
    operand: BoolExpr
    raw: str = ""


@dataclass(frozen=True)
class Compare(BoolExpr):
    left: ValueLike
    operator: str
    right: ValueLike
    raw: str = ""


@dataclass(frozen=True)
class IdentifierPredicate(BoolExpr):
    name: str
    raw: str = ""


@dataclass(frozen=True)
class CallPredicate(BoolExpr):
    function: str
    arguments: Tuple[str, ...]
    raw: str = ""

    @property
    def call_text(self) -> str:
        return self.raw or f"{self.function}({', '.join(self.arguments)})"


@dataclass(frozen=True)
class BitmaskPredicate(BoolExpr):
    value: ValueLike
    mask: ValueLike
    operator: str = "!="
    expected: ValueLike = "0"
    raw: str = ""


@dataclass(frozen=True)
class UnknownPredicate(BoolExpr):
    raw: str
    node_type: str = "unknown"


@dataclass(frozen=True)
class RangePredicate(BoolExpr):
    value: ValueLike
    lower: ValueLike
    upper: ValueLike
    upper_inclusive: bool = False
    raw: str = ""


@dataclass(frozen=True)
class AccessPredicate(BoolExpr):
    base: ValueLike
    offset: ValueLike
    extent: ValueLike
    width: ValueLike = "1"
    valid: bool = True
    raw: str = ""


@dataclass(frozen=True)
class OverflowPredicate(BoolExpr):
    expression: ValueLike
    type_name: str = ""
    overflows: bool = True
    raw: str = ""


@dataclass(frozen=True)
class InitializedPredicate(BoolExpr):
    value: ValueLike
    initialized: bool = True
    raw: str = ""


@dataclass(frozen=True)
class AlivePredicate(BoolExpr):
    value: ValueLike
    alive: bool = True
    raw: str = ""


@dataclass(frozen=True)
class ProgressPredicate(BoolExpr):
    value: ValueLike
    progresses: bool = True
    raw: str = ""


_INVERT_COMPARE = {
    "<": ">=",
    "<=": ">",
    ">": "<=",
    ">=": "<",
    "==": "!=",
    "!=": "==",
}


def and_expr(terms: Iterable[BoolExpr], raw: str = "") -> BoolExpr:
    """Build a flattened conjunction without discarding unknown predicates."""
    flattened = []
    for term in terms:
        flattened.extend(term.terms if isinstance(term, And) else (term,))
    if len(flattened) == 1:
        return flattened[0]
    return And(tuple(flattened), raw=raw)


def or_expr(terms: Iterable[BoolExpr], raw: str = "") -> BoolExpr:
    """Build a flattened disjunction without changing its logical meaning."""
    flattened = []
    for term in terms:
        flattened.extend(term.terms if isinstance(term, Or) else (term,))
    if len(flattened) == 1:
        return flattened[0]
    return Or(tuple(flattened), raw=raw)


def negate(expr: BoolExpr) -> BoolExpr:
    """Negate *expr* and push NOT through supported boolean operators."""
    if isinstance(expr, Not):
        return expr.operand
    if isinstance(expr, And):
        return or_expr((negate(term) for term in expr.terms), raw=expr.raw)
    if isinstance(expr, Or):
        return and_expr((negate(term) for term in expr.terms), raw=expr.raw)
    if isinstance(expr, Compare) and expr.operator in _INVERT_COMPARE:
        return Compare(expr.left, _INVERT_COMPARE[expr.operator], expr.right, raw=expr.raw)
    if isinstance(expr, BitmaskPredicate) and expr.operator in _INVERT_COMPARE:
        return BitmaskPredicate(
            expr.value,
            expr.mask,
            _INVERT_COMPARE[expr.operator],
            expr.expected,
            raw=expr.raw,
        )
    return Not(expr, raw=expr.raw)


def walk_expr(expr: BoolExpr) -> Iterable[BoolExpr]:
    """Yield an expression and all of its nested expressions."""
    yield expr
    if isinstance(expr, (And, Or)):
        for term in expr.terms:
            yield from walk_expr(term)
    elif isinstance(expr, Not):
        yield from walk_expr(expr.operand)


def _precedence(expr: BoolExpr) -> int:
    if isinstance(expr, Or):
        return 1
    if isinstance(expr, And):
        return 2
    if isinstance(expr, Not):
        return 3
    return 4


def _render(expr: BoolExpr, parent_precedence: int = 0) -> str:
    if isinstance(expr, And):
        text = " && ".join(_render(term, _precedence(expr)) for term in expr.terms)
    elif isinstance(expr, Or):
        text = " || ".join(_render(term, _precedence(expr)) for term in expr.terms)
    elif isinstance(expr, Not):
        operand = _render(expr.operand, _precedence(expr))
        if isinstance(expr.operand, (IdentifierPredicate, CallPredicate)):
            text = f"!{operand}"
        else:
            text = f"!({operand})"
    elif isinstance(expr, Compare):
        text = f"{render_value(expr.left)} {expr.operator} {render_value(expr.right)}"
    elif isinstance(expr, IdentifierPredicate):
        text = expr.name
    elif isinstance(expr, CallPredicate):
        text = expr.call_text
    elif isinstance(expr, BitmaskPredicate):
        text = (
            f"({render_value(expr.value)} & {render_value(expr.mask)}) "
            f"{expr.operator} {render_value(expr.expected)}"
        )
    elif isinstance(expr, RangePredicate):
        upper_op = "<=" if expr.upper_inclusive else "<"
        text = (
            f"{render_value(expr.lower)} <= {render_value(expr.value)} && "
            f"{render_value(expr.value)} {upper_op} {render_value(expr.upper)}"
        )
    elif isinstance(expr, AccessPredicate):
        relation = "within" if expr.valid else "outside"
        text = (
            f"access({render_value(expr.base)}, offset={render_value(expr.offset)}, "
            f"width={render_value(expr.width)}) {relation} extent {render_value(expr.extent)}"
        )
    elif isinstance(expr, OverflowPredicate):
        relation = "overflows" if expr.overflows else "does_not_overflow"
        suffix = f" as {expr.type_name}" if expr.type_name else ""
        text = f"{render_value(expr.expression)} {relation}{suffix}"
    elif isinstance(expr, InitializedPredicate):
        text = f"{render_value(expr.value)} is {'initialized' if expr.initialized else 'uninitialized'}"
    elif isinstance(expr, AlivePredicate):
        text = f"{render_value(expr.value)} is {'alive' if expr.alive else 'not_alive'}"
    elif isinstance(expr, ProgressPredicate):
        text = f"{render_value(expr.value)} {'progresses' if expr.progresses else 'does_not_progress'}"
    elif isinstance(expr, UnknownPredicate):
        text = expr.raw or "<unknown>"
    else:  # Defensive fallback for future IR nodes.
        text = str(expr)

    if _precedence(expr) < parent_precedence:
        return f"({text})"
    return text


def expr_to_dict(expr: BoolExpr) -> dict[str, Any]:
    """Return a JSON-friendly representation for evidence/debug consumers."""
    if isinstance(expr, (And, Or)):
        return {
            "kind": type(expr).__name__,
            "terms": [expr_to_dict(term) for term in expr.terms],
            "raw": expr.raw,
        }
    if isinstance(expr, Not):
        return {"kind": "Not", "operand": expr_to_dict(expr.operand), "raw": expr.raw}
    data = {"kind": type(expr).__name__}
    for key, value in expr.__dict__.items():
        data[key] = value_to_dict(value)
    if isinstance(expr, CallPredicate):
        data["arguments"] = list(expr.arguments)
    return data


def render_value(value: ValueLike) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, RawValue):
        return value.raw
    if isinstance(value, IdentifierValue):
        return value.raw or value.name
    if isinstance(value, LiteralValue):
        return value.raw or value.value
    if isinstance(value, MemberValue):
        return value.raw or f"{render_value(value.base)}{value.operator}{value.member}"
    if isinstance(value, SubscriptValue):
        return value.raw or f"{render_value(value.base)}[{render_value(value.index)}]"
    if isinstance(value, DereferenceValue):
        return value.raw or f"*{render_value(value.operand)}"
    if isinstance(value, CallValue):
        return value.raw or f"{value.function}({', '.join(render_value(arg) for arg in value.arguments)})"
    if isinstance(value, CastValue):
        return value.raw or f"({value.type_name}) {render_value(value.operand)}"
    if isinstance(value, SizeofValue):
        return value.raw or f"sizeof({render_value(value.operand)})"
    if isinstance(value, UnaryValue):
        return value.raw or f"{value.operator}{render_value(value.operand)}"
    if isinstance(value, BinaryValue):
        return value.raw or f"{render_value(value.left)} {value.operator} {render_value(value.right)}"
    return str(value)


def value_to_dict(value: Any) -> Any:
    if isinstance(value, ValueExpr):
        return {
            "kind": type(value).__name__,
            **{key: value_to_dict(item) for key, item in value.__dict__.items()},
        }
    if isinstance(value, BoolExpr):
        return expr_to_dict(value)
    if isinstance(value, tuple):
        return [value_to_dict(item) for item in value]
    if isinstance(value, list):
        return [value_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: value_to_dict(item) for key, item in value.items()}
    return value
