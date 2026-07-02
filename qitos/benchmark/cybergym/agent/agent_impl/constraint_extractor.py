"""Target-relative control-dependence and rejecting-guard extraction.

This module deliberately stops short of building a CFG.  It anchors every
result to a real target call expression and only emits conditions whose AST
relationship proves (or conservatively suggests) that the call is reachable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional, Set

from .constraint_ast import (
    ParsedSource,
    SourceSpan,
    call_matches,
    callee_leaf,
    contains,
    descendants,
    enclosing_function,
    expression_to_ir,
    function_matches,
    parse_source,
    target_span_matches,
    unwrap_condition,
)
from .constraint_formats import extract_format_details
from .constraint_models import ConstraintCandidate
from .constraint_ir import (
    BitmaskPredicate,
    BoolExpr,
    Compare,
    UnknownPredicate,
    and_expr,
    expr_to_dict,
    or_expr,
    walk_expr,
)


DEFAULT_NORETURN_FUNCTIONS = frozenset({
    "abort",
    "exit",
    "_Exit",
    "quick_exit",
    "fatal",
    "panic",
    "__builtin_trap",
    "__assert_fail",
    "std::terminate",
    "terminate",
})


class Termination(Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def _branch_body(node: Any) -> Any:
    if node is None:
        return None
    if node.type == "else_clause" and node.named_children:
        return node.named_children[-1]
    return node


def must_terminate(node: Any, parsed: ParsedSource, noreturn_functions: Set[str]) -> Termination:
    """Return whether every path through *node* exits the current function."""
    node = _branch_body(node)
    if node is None:
        return Termination.NO
    if node.type in {"return_statement", "throw_statement"}:
        return Termination.YES
    if node.type in {"break_statement", "continue_statement", "goto_statement"}:
        return Termination.UNKNOWN
    if node.type == "expression_statement":
        expressions = node.named_children
        if len(expressions) == 1 and expressions[0].type == "call_expression":
            call = expressions[0]
            full_name = parsed.text(call.child_by_field_name("function")).strip()
            if (
                callee_leaf(call, parsed) in noreturn_functions
                or full_name in noreturn_functions
                or callee_leaf(call, parsed) in parsed.noreturn_macros
            ):
                return Termination.YES
        return Termination.NO
    if node.type == "if_statement":
        consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        if alternative is None:
            return Termination.NO
        left = must_terminate(consequence, parsed, noreturn_functions)
        right = must_terminate(alternative, parsed, noreturn_functions)
        if left is Termination.YES and right is Termination.YES:
            return Termination.YES
        if Termination.UNKNOWN in {left, right}:
            return Termination.UNKNOWN
        return Termination.NO
    if node.type in {"compound_statement", "else_clause", "labeled_statement"}:
        if not node.named_children:
            return Termination.NO
        return must_terminate(node.named_children[-1], parsed, noreturn_functions)
    return Termination.NO


def _confidence(
    *,
    local_has_error: bool,
    explicit_target: bool,
    caller_resolved: bool,
    base: str,
) -> tuple[str, float]:
    levels = {"low": 0, "medium": 1, "high": 2}
    scores = {"low": 0.35, "medium": 0.68, "high": 0.9}
    level = levels[base]
    if not explicit_target or not caller_resolved:
        level -= 1
    if local_has_error:
        level -= 1
    name = ("low", "medium", "high")[max(0, min(level, 2))]
    return name, scores[name]


def _gate_type(expr: BoolExpr, format_details: list[dict[str, Any]], origin: str) -> str:
    if format_details:
        return "format_gate"
    if origin == "switch_dispatch":
        return "dispatch_gate"
    nested = list(walk_expr(expr))
    if any(isinstance(item, BitmaskPredicate) for item in nested):
        return "value_gate"
    if any(isinstance(item, Compare) and item.operator in {"<", "<=", ">", ">="} for item in nested):
        return "bounds_gate"
    if any(isinstance(item, Compare) for item in nested):
        return "value_gate"
    return "path_gate"


def _make_candidate(
    *,
    expr: BoolExpr,
    condition_node: Any,
    evidence_node: Any,
    call_node: Any,
    parsed: ParsedSource,
    source_path: str,
    caller_function: str,
    target_function: str,
    origin: str,
    base_confidence: str,
    explicit_target: bool,
    caller_resolved: bool,
    notes: Optional[list[str]] = None,
) -> ConstraintCandidate:
    formula = expr.render()
    contains_unknown = any(isinstance(item, UnknownPredicate) for item in walk_expr(expr))
    if contains_unknown and base_confidence == "high":
        base_confidence = "medium"
    format_details = extract_format_details(condition_node, expr, parsed)
    gate_type = _gate_type(expr, format_details, origin)
    function_node, _ = enclosing_function(call_node, parsed)
    incomplete_function = bool(
        function_node is not None
        and function_node.has_error
        and function_node.end_byte >= len(parsed.source)
    )
    local_parse_damage = (
        parsed.local_has_error(condition_node)
        or parsed.local_has_error(call_node)
        or incomplete_function
    )
    confidence, score = _confidence(
        local_has_error=local_parse_damage,
        explicit_target=explicit_target,
        caller_resolved=caller_resolved,
        base=base_confidence,
    )
    call_span = parsed.span(call_node)
    evidence_span = parsed.span(evidence_node)
    raw_node = unwrap_condition(condition_node)
    raw_condition = parsed.text(raw_node if raw_node is not None else condition_node).strip()
    target_label = target_function or callee_leaf(call_node, parsed)
    description = (
        f"To reach {target_label} at {source_path}:{call_span.start_line}:{call_span.start_column}, "
        f"require {formula} ({origin})"
    )
    evidence_notes = list(notes or [])
    if contains_unknown:
        evidence_notes.append("condition contains an opaque expression or macro; semantic confidence downgraded")
    if local_parse_damage:
        evidence_notes.append(
            "Tree-sitter parse damage overlaps the local constraint evidence; confidence downgraded"
        )
    if not caller_resolved:
        evidence_notes.append("enclosing caller definition was not present in the parsed source; confidence downgraded")
    return ConstraintCandidate(
        gate_type=gate_type,
        description=description,
        required_condition=formula,
        polarity="satisfy",
        confidence=confidence,
        source=source_path,
        node_function=caller_function,
        normalized_formula=formula,
        raw_condition=raw_condition,
        source_span=evidence_span,
        enclosing_function=caller_function,
        target_function=target_label,
        target_call_span=call_span,
        origin=origin,
        control_origin=origin,
        confidence_score=score,
        structured_formula=expr_to_dict(expr),
        format_details=format_details,
        notes=evidence_notes,
    )


def _enclosing_requirements(call_node: Any, parsed: ParsedSource) -> Iterable[tuple[BoolExpr, Any, Any, str, str, list[str]]]:
    current = call_node.parent
    while current is not None:
        if current.type == "if_statement":
            condition = current.child_by_field_name("condition")
            consequence = current.child_by_field_name("consequence")
            alternative = current.child_by_field_name("alternative")
            if condition is not None and consequence is not None:
                expr = expression_to_ir(condition, parsed)
                if contains(consequence, call_node):
                    yield expr, condition, condition, "enclosing_branch", "high", []
                elif alternative is not None and contains(alternative, call_node):
                    yield expr.negate(), condition, condition, "enclosing_branch", "high", []
        elif current.type in {"while_statement", "for_statement"}:
            condition = current.child_by_field_name("condition")
            body = current.child_by_field_name("body")
            if condition is not None and body is not None and contains(body, call_node):
                notes: list[str] = []
                if current.type == "for_statement":
                    initializer = current.child_by_field_name("initializer")
                    if initializer is not None:
                        names = {
                            parsed.text(item).strip()
                            for item in descendants(initializer, "identifier")
                        }
                        condition_text = parsed.text(condition)
                        induction = sorted(name for name in names if name and name in condition_text)
                        if induction:
                            notes.append(f"loop induction variable(s): {', '.join(induction)}")
                yield expression_to_ir(condition, parsed), condition, condition, "loop_entry", "medium", notes
        if current.type == "function_definition":
            break
        current = current.parent


def _guard_requirement(
    if_node: Any,
    call_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> Optional[tuple[BoolExpr, Any]]:
    condition = if_node.child_by_field_name("condition")
    consequence = if_node.child_by_field_name("consequence")
    alternative = if_node.child_by_field_name("alternative")
    if condition is None or consequence is None:
        return None
    consequence_term = _branch_blocks_anchor(consequence, call_node, parsed, noreturn_functions)
    alternative_term = _branch_blocks_anchor(alternative, call_node, parsed, noreturn_functions)
    expr = expression_to_ir(condition, parsed)
    if consequence_term is Termination.YES and alternative_term is Termination.NO:
        return expr.negate(), condition
    if alternative_term is Termination.YES and consequence_term is Termination.NO:
        return expr, condition
    return None


def _goto_destination(node: Any, parsed: ParsedSource) -> Any:
    identifiers = list(descendants(node, "statement_identifier"))
    if not identifiers:
        return None
    label = parsed.text(identifiers[-1]).strip()
    function, _name = enclosing_function(node, parsed)
    if function is None:
        return None
    for statement in descendants(function, "labeled_statement"):
        label_node = statement.child_by_field_name("label")
        if label_node is None:
            label_ids = list(descendants(statement, "statement_identifier"))
            label_node = label_ids[0] if label_ids else None
        if label_node is not None and parsed.text(label_node).strip() == label:
            return statement
    return None


def _branch_blocks_anchor(
    node: Any,
    call_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> Termination:
    """Whether taking a branch proves the anchor cannot be reached next.

    This is a small control-flow skeleton: function exits, loop transfers, and
    forward gotos are modeled; unresolved/backward jumps remain unknown.
    """
    node = _branch_body(node)
    if node is None:
        return Termination.NO
    ordinary = must_terminate(node, parsed, noreturn_functions)
    if ordinary is Termination.YES:
        return Termination.YES
    if node.type in {"break_statement", "continue_statement"}:
        # A later loop iteration may still reach the anchor, so this is not a
        # function-level input requirement without induction/value analysis.
        return Termination.UNKNOWN
    if node.type == "goto_statement":
        destination = _goto_destination(node, parsed)
        if destination is None:
            return Termination.UNKNOWN
        return Termination.YES if destination.start_byte > call_node.end_byte else Termination.UNKNOWN
    if node.type in {"compound_statement", "else_clause", "labeled_statement"}:
        if not node.named_children:
            return Termination.NO
        return _branch_blocks_anchor(node.named_children[-1], call_node, parsed, noreturn_functions)
    if node.type == "if_statement":
        consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        if alternative is None:
            return Termination.NO
        left = _branch_blocks_anchor(consequence, call_node, parsed, noreturn_functions)
        right = _branch_blocks_anchor(alternative, call_node, parsed, noreturn_functions)
        if left is Termination.YES and right is Termination.YES:
            return Termination.YES
        if Termination.UNKNOWN in {left, right}:
            return Termination.UNKNOWN
    return Termination.NO


def _preceding_guards(
    call_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> Iterable[tuple[BoolExpr, Any, Any, str, str, list[str]]]:
    """Scan only preceding sibling statements in each comparable AST scope."""
    current = call_node
    while current.parent is not None:
        parent = current.parent
        if parent.type in {"compound_statement", "case_statement"}:
            children = list(parent.named_children)
            try:
                index = children.index(current)
            except ValueError:
                index = next((i for i, item in enumerate(children) if contains(item, current)), -1)
            if index >= 0:
                for sibling in children[:index]:
                    if sibling.type != "if_statement":
                        continue
                    required = _guard_requirement(sibling, call_node, parsed, noreturn_functions)
                    if required is not None:
                        expr, condition = required
                        yield expr, condition, condition, "preceding_guard", "high", []
        if parent.type == "function_definition":
            break
        current = parent


def _has_unconditional_blocking_predecessor(
    call_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> bool:
    """Reject lexically dead target calls without pretending to build a CFG."""
    current = call_node
    while current.parent is not None:
        parent = current.parent
        if parent.type in {"compound_statement", "case_statement"}:
            children = list(parent.named_children)
            try:
                index = children.index(current)
            except ValueError:
                index = next((i for i, item in enumerate(children) if contains(item, current)), -1)
            if index >= 0:
                for sibling in children[:index]:
                    if sibling.type in {
                        "return_statement",
                        "throw_statement",
                        "break_statement",
                        "continue_statement",
                        "goto_statement",
                    }:
                        return True
                    if must_terminate(sibling, parsed, noreturn_functions) is Termination.YES:
                        return True
        if parent.type == "function_definition":
            break
        current = parent
    return False


def _case_fallthrough_status(
    case_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> Termination:
    """Classify whether a preceding case definitely reaches the next label.

    ``NO`` means definite fallthrough, ``YES`` means definitely blocked, and
    ``UNKNOWN`` avoids emitting an incomplete switch formula.
    """
    value = case_node.child_by_field_name("value")
    statements = [
        child for child in case_node.named_children
        if value is None
        or child.start_byte != value.start_byte
        or child.end_byte != value.end_byte
    ]
    if not statements:
        return Termination.NO
    simple_fallthrough = {"declaration", "expression_statement", "empty_statement"}
    for statement in statements:
        if statement.type in {
            "return_statement",
            "throw_statement",
            "break_statement",
            "continue_statement",
            "goto_statement",
        }:
            return Termination.YES
        termination = must_terminate(statement, parsed, noreturn_functions)
        if termination is Termination.YES:
            return Termination.YES
        if statement.type not in simple_fallthrough:
            return Termination.UNKNOWN
    return Termination.NO


def _switch_route_expr(
    case_node: Any,
    cases: list[Any],
    switch_text: str,
    raw: str,
    parsed: ParsedSource,
) -> Optional[BoolExpr]:
    value_node = case_node.child_by_field_name("value")
    if value_node is not None:
        return Compare(switch_text, "==", parsed.text(value_node).strip(), raw=raw)
    explicit_values = [
        parsed.text(value).strip()
        for item in cases
        if (value := item.child_by_field_name("value")) is not None
    ]
    if not explicit_values:
        return None
    return and_expr(Compare(switch_text, "!=", value, raw=raw) for value in explicit_values)


def _switch_requirement(
    call_node: Any,
    parsed: ParsedSource,
    noreturn_functions: Set[str],
) -> Optional[tuple[BoolExpr, Any, Any, str, str, list[str]]]:
    case_node = None
    switch_node = None
    current = call_node.parent
    while current is not None:
        if case_node is None and current.type == "case_statement":
            case_node = current
        if current.type == "switch_statement":
            switch_node = current
            break
        if current.type == "function_definition":
            break
        current = current.parent
    if case_node is None or switch_node is None:
        return None

    condition = switch_node.child_by_field_name("condition")
    body = switch_node.child_by_field_name("body")
    if condition is None or body is None:
        return None
    switch_expr_node = unwrap_condition(condition)
    switch_text = parsed.text(switch_expr_node if switch_expr_node is not None else condition).strip()
    cases = [child for child in body.named_children if child.type == "case_statement"]
    try:
        case_index = cases.index(case_node)
    except ValueError:
        return None

    raw = parsed.text(condition).strip()
    current_route = _switch_route_expr(case_node, cases, switch_text, raw, parsed)
    if current_route is None:
        return None
    routes = [current_route]
    previous = case_index - 1
    while previous >= 0:
        flow = _case_fallthrough_status(cases[previous], parsed, noreturn_functions)
        if flow is Termination.YES:
            break
        if flow is Termination.UNKNOWN:
            return None
        prior_route = _switch_route_expr(cases[previous], cases, switch_text, raw, parsed)
        if prior_route is None:
            return None
        routes.insert(0, prior_route)
        previous -= 1
    expr = or_expr(routes)
    return expr, condition, case_node, "switch_dispatch", "high", []


def _candidate_key(candidate: ConstraintCandidate) -> tuple[Any, ...]:
    call_span = candidate.target_call_span
    return (
        candidate.node_function,
        candidate.target_function,
        call_span.start_byte if call_span else -1,
        call_span.end_byte if call_span else -1,
        candidate.origin,
        candidate.normalized_formula,
    )


def extract_path_constraints(
    source_text: str | bytes,
    source_path: str = "",
    known_functions: Optional[Set[str]] = None,
    file_extension: str = ".c",
    *,
    caller_function: str = "",
    target_function: str = "",
    target_callsite: Any = None,
    language: Optional[str] = None,
    noreturn_functions: Optional[Set[str]] = None,
    source_line_offset: int = 0,
    parsed_source: Optional[ParsedSource] = None,
    max_target_callsites: Optional[int] = None,
) -> list[ConstraintCandidate]:
    """Extract reachability constraints for exact calls to ``target_function``.

    ``known_functions`` is retained as a legacy adapter only.  It is used when
    it contains exactly one function; ambiguous sets intentionally yield no
    candidates rather than reviving whole-file heuristic scanning.
    """
    explicit_target = bool(target_function.strip())
    legacy_targets = {item for item in (known_functions or set()) if str(item).strip()}
    if not explicit_target:
        if len(legacy_targets) != 1:
            return []
        target_function = next(iter(legacy_targets))

    parsed = parsed_source or parse_source(
        source_text, file_extension=file_extension, language=language, line_offset=source_line_offset,
    )
    if parsed is None:
        # Regex cannot establish control dependence, so failure is deliberately quiet and safe.
        return []

    noreturn = set(DEFAULT_NORETURN_FUNCTIONS)
    noreturn.update(noreturn_functions or set())

    target_calls = []
    for call in descendants(parsed.root, "call_expression"):
        if not call_matches(call, target_function, parsed):
            continue
        if not target_span_matches(call, parsed, target_callsite):
            continue
        function_node, actual_caller = enclosing_function(call, parsed)
        if caller_function and actual_caller and not function_matches(actual_caller, caller_function):
            continue
        target_calls.append((call, actual_caller))
        if max_target_callsites is not None and len(target_calls) >= max_target_callsites:
            break

    results: list[ConstraintCandidate] = []
    seen: set[tuple[Any, ...]] = set()
    for call, actual_caller in target_calls:
        if _has_unconditional_blocking_predecessor(call, parsed, noreturn):
            continue
        caller = caller_function or actual_caller
        caller_resolved = bool(actual_caller and function_matches(actual_caller, caller))
        if not actual_caller and caller_function:
            # A bounded READ snippet may omit the function declaration.  The
            # explicit chain edge still anchors the call, at reduced confidence.
            caller_resolved = False

        requirements = [*_enclosing_requirements(call, parsed), *_preceding_guards(call, parsed, noreturn)]
        switch_requirement = _switch_requirement(call, parsed, noreturn)
        if switch_requirement is not None:
            requirements.append(switch_requirement)

        for expr, condition, evidence, origin, base_confidence, notes in requirements:
            candidate = _make_candidate(
                expr=expr,
                condition_node=condition,
                evidence_node=evidence,
                call_node=call,
                parsed=parsed,
                source_path=source_path,
                caller_function=caller,
                target_function=target_function,
                origin=origin,
                base_confidence=base_confidence,
                explicit_target=explicit_target,
                caller_resolved=caller_resolved,
                notes=notes,
            )
            # Format semantics are evidence attached to a real control edge,
            # never a whole-if/body scan.
            if candidate.format_details:
                candidate.control_origin = candidate.origin
                candidate.origin = "format_check"
            key = _candidate_key(candidate)
            if key not in seen:
                seen.add(key)
                results.append(candidate)

    return sorted(
        results,
        key=lambda item: (
            item.target_call_span.start_byte if item.target_call_span else -1,
            item.source_span.start_byte if item.source_span else -1,
            item.origin,
        ),
    )


def extract_callsite_constraints(
    call: Any,
    parsed: ParsedSource,
    *,
    source_path: str = "",
    caller_function: str = "",
    target_function: str = "",
    noreturn_functions: Optional[Set[str]] = None,
) -> list[ConstraintCandidate]:
    """Linear-time adapter for an already located Tree-sitter call node."""
    noreturn = set(DEFAULT_NORETURN_FUNCTIONS)
    noreturn.update(noreturn_functions or set())
    if _has_unconditional_blocking_predecessor(call, parsed, noreturn):
        return []
    _function_node, actual_caller = enclosing_function(call, parsed)
    caller = caller_function or actual_caller
    requirements = [*_enclosing_requirements(call, parsed), *_preceding_guards(call, parsed, noreturn)]
    switch_requirement = _switch_requirement(call, parsed, noreturn)
    if switch_requirement is not None:
        requirements.append(switch_requirement)
    results: list[ConstraintCandidate] = []
    seen: set[tuple[Any, ...]] = set()
    for expr, condition, evidence, origin, base_confidence, notes in requirements:
        candidate = _make_candidate(
            expr=expr, condition_node=condition, evidence_node=evidence,
            call_node=call, parsed=parsed, source_path=source_path,
            caller_function=caller, target_function=target_function or callee_leaf(call, parsed),
            origin=origin, base_confidence=base_confidence, explicit_target=True,
            caller_resolved=bool(actual_caller), notes=notes,
        )
        if candidate.format_details:
            candidate.control_origin = candidate.origin
            candidate.origin = "format_check"
        key = _candidate_key(candidate)
        if key not in seen:
            seen.add(key); results.append(candidate)
    return results


def _extract_format_gates_regex(source_text: str, source_path: str) -> list[ConstraintCandidate]:
    """Deprecated compatibility stub.

    Regex-only extraction cannot prove that a comparison controls a target
    callsite, so the safe fallback is an empty candidate list.
    """
    return []


def analyze_constraints(request: Any):
    """Public lazy export for the request/result API, avoiding import cycles."""
    from .constraint_analysis import analyze_constraints as analyze
    return analyze(request)


__all__ = [
    "ConstraintCandidate",
    "DEFAULT_NORETURN_FUNCTIONS",
    "SourceSpan",
    "Termination",
    "analyze_constraints",
    "extract_path_constraints",
    "must_terminate",
]
