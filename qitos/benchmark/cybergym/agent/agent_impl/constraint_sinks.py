"""Source-only sink-relative trigger and hazard detectors for C/C++."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .constraint_ast import (
    ParsedSource,
    SourceSpan,
    argument_texts,
    callee_leaf,
    descendants,
    expression_to_ir,
    function_matches,
    function_name,
    unwrap_condition,
    value_to_ir,
    walk,
)
from .constraint_ir import (
    AccessPredicate,
    AlivePredicate,
    And,
    BinaryValue,
    CallValue,
    BoolExpr,
    Compare,
    InitializedPredicate,
    Not,
    Or,
    OverflowPredicate,
    ProgressPredicate,
    RangePredicate,
    UnknownPredicate,
    ValueLike,
    and_expr,
    expr_to_dict,
    or_expr,
    render_value,
)
from .constraint_models import (
    AnalysisBudget,
    ApiModelConfig,
    BudgetContext,
    BudgetExceeded,
    ConstraintCandidate,
    ExtractionDiagnostic,
    VulnerabilityHint,
    stable_path_id,
)


_UNBOUNDED_COPY_CALLS = {"strcpy", "strcat", "sprintf", "vsprintf", "gets"}


@dataclass
class SinkAnalysis:
    candidates: list[ConstraintCandidate]
    diagnostics: list[ExtractionDiagnostic]
    resolved: bool
    anchors: int
    truncated: bool = False


def _node_text(parsed: ParsedSource, node: Any) -> str:
    return parsed.text(node).strip() if node is not None else ""


def _identifiers(node: Any, parsed: ParsedSource) -> list[str]:
    values = []
    for item in walk(node):
        if item.type == "identifier":
            value = parsed.text(item).strip()
            if value and value not in values:
                values.append(value)
    return values


def _local_confidence(
    parsed: ParsedSource,
    node: Any,
    completeness: str,
    *,
    proven: bool,
    hint_match: bool,
) -> tuple[str, float, list[str]]:
    reasons = ["source AST evidence"]
    level = 2 if proven and hint_match else 1 if proven or hint_match else 0
    if parsed.local_has_error(node):
        level = min(level, 0)
        reasons.append("local parse damage")
    if completeness == "snippet":
        level = min(level, 0)
        reasons.append("incomplete source snippet")
    names = ("low", "medium", "high")
    scores = (0.35, 0.68, 0.9)
    return names[level], scores[level], reasons


def _candidate(
    *,
    parsed: ParsedSource,
    node: Any,
    function: str,
    source_path: str,
    expr: BoolExpr,
    safe_expr: Optional[BoolExpr],
    role: str,
    gate_type: str,
    origin: str,
    access_mode: str,
    hint_match: bool,
    proven: bool,
    completeness: str,
    tags: Iterable[str],
    description: str,
) -> ConstraintCandidate:
    span = parsed.span(node)
    confidence, score, reasons = _local_confidence(
        parsed,
        node,
        completeness,
        proven=proven,
        hint_match=hint_match,
    )
    formula = expr.render()
    path_id = stable_path_id("sink", source_path, function, function, span)
    return ConstraintCandidate(
        gate_type=gate_type,
        description=description,
        required_condition=formula,
        polarity="satisfy",
        confidence=confidence,
        source=source_path,
        node_function=function,
        normalized_formula=formula,
        raw_condition=_node_text(parsed, node),
        source_span=span,
        enclosing_function=function,
        origin=origin,
        control_origin="sink_analysis",
        confidence_score=score,
        structured_formula=expr_to_dict(expr),
        role=role,
        path_id=path_id,
        sink_span=span,
        access_mode=access_mode,
        required_formula=formula,
        safe_formula=safe_expr.render() if safe_expr is not None else "",
        violation_formula=formula if role == "trigger" else "",
        promotable=False,
        confidence_reasons=reasons,
        symbol_dependencies=_identifiers(node, parsed),
        semantic_tags=list(dict.fromkeys(tags)),
    )


def _function_nodes(parsed: ParsedSource, requested: str, hint: VulnerabilityHint) -> list[Any]:
    functions = list(descendants(parsed.root, "function_definition"))
    names = [requested, *hint.symbols]
    names = [name for name in names if name]
    if names:
        matched = [
            node for node in functions
            if any(function_matches(function_name(node, parsed), name) for name in names)
        ]
        if matched:
            return matched
    if requested:
        return []
    return functions if len(functions) == 1 else []


def _declaration_facts(function: Any, parsed: ParsedSource) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Return fixed array extents, declared types, and function parameters."""
    extents: dict[str, str] = {}
    types: dict[str, str] = {}
    parameters: set[str] = set()
    declarator = function.child_by_field_name("declarator")
    if declarator is not None:
        for param in descendants(declarator, "parameter_declaration"):
            ids = list(descendants(param, "identifier"))
            if ids:
                name = _node_text(parsed, ids[-1])
                parameters.add(name)
                type_node = param.child_by_field_name("type")
                if type_node is not None:
                    types[name] = _node_text(parsed, type_node)

    for declaration in descendants(function, "declaration"):
        type_node = declaration.child_by_field_name("type")
        type_text = _node_text(parsed, type_node)
        for array in descendants(declaration, "array_declarator"):
            name_node = array.child_by_field_name("declarator")
            size_node = array.child_by_field_name("size")
            if name_node is not None and size_node is not None:
                name_ids = list(descendants(name_node, "identifier"))
                name = _node_text(parsed, name_ids[-1] if name_ids else name_node)
                extents[name] = _node_text(parsed, size_node)
                if type_text:
                    types[name] = type_text
        for ident in descendants(declaration, "identifier"):
            name = _node_text(parsed, ident)
            if type_text and name not in types:
                types[name] = type_text
    return extents, types, parameters


def _signature_buffer_extents(function: Any, parsed: ParsedSource) -> dict[str, str]:
    """Pair adjacent pointer + scalar parameters structurally, never by name."""
    declarator = function.child_by_field_name("declarator")
    if declarator is None:
        return {}
    parameters = list(descendants(declarator, "parameter_declaration"))
    facts: list[tuple[str, bool, str]] = []
    for parameter in parameters:
        ids = list(descendants(parameter, "identifier"))
        if not ids:
            continue
        name = _node_text(parsed, ids[-1])
        text = _node_text(parsed, parameter)
        type_node = parameter.child_by_field_name("type")
        type_text = _node_text(parsed, type_node).lower()
        is_pointer = "*" in text or "[" in text
        facts.append((name, is_pointer, type_text))
    extents: dict[str, str] = {}
    for current, following in zip(facts, facts[1:]):
        pointer_name, is_pointer, _pointer_type = current
        extent_name, extent_pointer, extent_type = following
        integral_extent = any(token in extent_type for token in ("int", "size_t", "long", "short"))
        if is_pointer and not extent_pointer and integral_extent:
            extents[pointer_name] = extent_name
    return extents


def _input_derived_names(function: Any, parsed: ParsedSource, parameters: set[str]) -> set[str]:
    """Finite syntax-level taint propagation; names alone never imply input."""
    derived = set(parameters)
    changed = True
    rounds = 0
    while changed and rounds < 8:
        changed = False
        rounds += 1
        for init in descendants(function, "init_declarator"):
            declarator = init.child_by_field_name("declarator")
            value = init.child_by_field_name("value")
            left = list(descendants(declarator, "identifier")) if declarator is not None else []
            right = set(_identifiers(value, parsed)) if value is not None else set()
            if left and right.intersection(derived):
                name = _node_text(parsed, left[-1])
                if name not in derived:
                    derived.add(name)
                    changed = True
        for assignment in descendants(function, "assignment_expression"):
            left_node = assignment.child_by_field_name("left")
            right_node = assignment.child_by_field_name("right")
            right = set(_identifiers(right_node, parsed)) if right_node is not None else set()
            name = _assigned_scalar_name(left_node, parsed)
            if name and right.intersection(derived):
                if name not in derived:
                    derived.add(name)
                    changed = True
    return derived


def _assigned_scalar_name(node: Any, parsed: ParsedSource) -> str:
    current = node
    while current is not None and current.type in {"parenthesized_expression", "cast_expression"}:
        current = current.named_children[-1] if current.named_children else None
    return _node_text(parsed, current) if current is not None and current.type == "identifier" else ""


def _type_width(type_name: str) -> Optional[int]:
    lowered = type_name.lower().replace(" ", "")
    widths = {
        "int8_t": 8,
        "uint8_t": 8,
        "char": 8,
        "int16_t": 16,
        "uint16_t": 16,
        "short": 16,
        "int32_t": 32,
        "uint32_t": 32,
        "float": 32,
        "int64_t": 64,
        "uint64_t": 64,
        "longlong": 64,
        "double": 64,
    }
    for key, width in widths.items():
        if key in lowered:
            return width
    return None


def _fixed_integer_range(type_name: str) -> Optional[tuple[int, int]]:
    compact = type_name.lower().replace(" ", "")
    match = re.search(r"\b(u?)int(8|16|32|64)_t\b", compact)
    if not match:
        return None
    unsigned, width_text = match.groups()
    width = int(width_text)
    if unsigned:
        return 0, 1 << width
    return -(1 << (width - 1)), 1 << (width - 1)


def _base_identifier(value: ValueLike) -> str:
    text = render_value(value).strip()
    match = re.match(r"(?:\*|&)?([A-Za-z_]\w*)", text)
    return match.group(1) if match else ""


def _access_mode(node: Any) -> str:
    parent = node.parent
    if parent is not None and parent.type == "assignment_expression":
        left = parent.child_by_field_name("left")
        if left is not None and left.start_byte <= node.start_byte and node.end_byte <= left.end_byte:
            return "write"
    if parent is not None and parent.type == "update_expression":
        return "write"
    return "read"


def _literal_int(text: str) -> Optional[int]:
    value = text.strip().rstrip("uUlL")
    try:
        return int(value, 0)
    except ValueError:
        return None


def _guard_formulas(anchor: Any, parsed: ParsedSource) -> set[str]:
    """Collect simple source-proven conditions required to execute *anchor*."""
    formulas: set[str] = set()
    current = anchor.parent
    while current is not None:
        if current.type == "binary_expression":
            operator_node = current.child_by_field_name("operator")
            left = current.child_by_field_name("left")
            right = current.child_by_field_name("right")
            operator = _node_text(parsed, operator_node)
            if left is not None and right is not None and right.start_byte <= anchor.start_byte <= right.end_byte:
                if operator == "&&":
                    formulas.add(expression_to_ir(left, parsed).render())
                elif operator == "||":
                    formulas.add(expression_to_ir(left, parsed).negate().render())
        elif current.type == "if_statement":
            condition = current.child_by_field_name("condition")
            consequence = current.child_by_field_name("consequence")
            alternative = current.child_by_field_name("alternative")
            if condition is not None:
                expr = expression_to_ir(condition, parsed)
                if consequence is not None and consequence.start_byte <= anchor.start_byte <= consequence.end_byte:
                    formulas.add(expr.render())
                elif alternative is not None and alternative.start_byte <= anchor.start_byte <= alternative.end_byte:
                    formulas.add(expr.negate().render())
        elif current.type in {"while_statement", "for_statement"}:
            condition = current.child_by_field_name("condition")
            body = current.child_by_field_name("body")
            if condition is not None and body is not None and body.start_byte <= anchor.start_byte <= body.end_byte:
                formulas.add(expression_to_ir(condition, parsed).render())
            if current.type == "for_statement":
                initializer = current.child_by_field_name("initializer")
                update = current.child_by_field_name("update")
                init_text = _node_text(parsed, initializer)
                update_text = _node_text(parsed, update)
                match = re.search(r"\b([A-Za-z_]\w*)\s*=\s*(-?\d+)\b", init_text)
                if match and re.search(rf"(?:\+\+\s*{re.escape(match.group(1))}|{re.escape(match.group(1))}\s*\+\+|{re.escape(match.group(1))}\s*\+=\s*[1-9]\d*)", update_text):
                    formulas.add(f"{match.group(1)} >= {match.group(2)}")
        if current.type == "function_definition":
            break
        current = current.parent
    # Direct preceding reject guards in the same compound.
    current = anchor
    while current.parent is not None:
        parent = current.parent
        if parent.type == "compound_statement":
            children = list(parent.named_children)
            index = next((i for i, item in enumerate(children) if item is current or (
                item.start_byte <= current.start_byte and current.end_byte <= item.end_byte
            )), -1)
            for sibling in children[:max(index, 0)]:
                sibling_text = _node_text(parsed, sibling)
                assignment_fact = re.search(
                    r"\b([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\+\s*([1-9]\d*)\s*;?",
                    sibling_text,
                )
                if assignment_fact:
                    formulas.add(f"{assignment_fact.group(1)} >= {assignment_fact.group(2)}")
                if sibling.type != "if_statement":
                    continue
                condition = sibling.child_by_field_name("condition")
                consequence = sibling.child_by_field_name("consequence")
                alternative = sibling.child_by_field_name("alternative")
                if condition is None or consequence is None:
                    continue
                if _branch_exits(consequence):
                    formulas.add(expression_to_ir(condition, parsed).negate().render())
                elif alternative is not None and _branch_exits(alternative):
                    formulas.add(expression_to_ir(condition, parsed).render())
        if parent.type == "function_definition":
            break
        current = parent
    return formulas


def _branch_exits(node: Any) -> bool:
    """Small, deliberately strict branch-termination check for sink guards."""
    if node is None:
        return False
    if node.type in {"return_statement", "throw_statement", "goto_statement"}:
        return True
    if node.type in {"compound_statement", "else_clause"}:
        return bool(node.named_children) and _branch_exits(node.named_children[-1])
    if node.type == "if_statement":
        consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        return alternative is not None and _branch_exits(consequence) and _branch_exits(alternative)
    return False


def _strip_outer_parens(text: str) -> str:
    value = text.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, char in enumerate(value):
            depth += char == "("
            depth -= char == ")"
            if depth == 0:
                closes_at_end = index == len(value) - 1
                break
        if not closes_at_end:
            break
        value = value[1:-1].strip()
    return value


def _canonical_atom(text: str) -> str:
    value = re.sub(r"\s+", "", _strip_outer_parens(text)).replace("NULL", "0").replace("nullptr", "0")
    if re.fullmatch(r"!?[A-Za-z_]\w*", value):
        return f"{value[1:]}==0" if value.startswith("!") else f"{value}!=0"
    match = re.fullmatch(r"(.+?)(==|!=|<=|>=|<|>)(.+)", value)
    if not match:
        return value
    left, operator, right = match.groups()
    if re.fullmatch(r"(?:0x[0-9a-fA-F]+|\d+)", left) and re.fullmatch(r"[A-Za-z_]\w*", right):
        reverse = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
        left, right, operator = right, left, reverse[operator]
    return f"{left}{operator}{right}"


def _conjuncts(text: str) -> set[str]:
    # Guards emitted by the IR use explicit && for De Morgan-normalized
    # rejecting checks.  Do not split disjunctions: implication is unknown.
    return {_canonical_atom(part) for part in re.split(r"\s*&&\s*", _strip_outer_parens(text))}


def _is_guarded(anchor: Any, parsed: ParsedSource, safe_expr: BoolExpr) -> bool:
    available: set[str] = set()
    for formula in _guard_formulas(anchor, parsed):
        available.update(_conjuncts(formula))
    required = _conjuncts(safe_expr.render())
    if not required:
        return False
    if required.issubset(available):
        return True
    return all(atom in available or _comparison_implied(atom, available) for atom in required)


def _comparison_implied(required: str, available: set[str]) -> bool:
    def relation(atom: str) -> Optional[tuple[str, str, bool]]:
        match = re.fullmatch(r"(.+?)(<=|>=|<|>)(.+)", atom)
        if not match:
            return None
        left, operator, right = match.groups()
        if operator in {">", ">="}:
            left, right = right, left
            operator = "<" if operator == ">" else "<="
        return left, right, operator == "<"

    target = relation(required)
    if target is None:
        return False
    start, finish, strict_required = target
    graph: dict[str, list[tuple[str, bool]]] = {}
    for atom in available:
        edge = relation(atom)
        if edge is not None:
            graph.setdefault(edge[0], []).append((edge[1], edge[2]))
    # A source expression X-k is strictly below X for a positive literal k.
    nodes = {start, finish, *(item for edge in graph.items() for item in (edge[0], *(target for target, _ in edge[1])))}
    numeric_nodes = sorted({int(node) for node in nodes if re.fullmatch(r"-?\d+", node)})
    for left, right in zip(numeric_nodes, numeric_nodes[1:]):
        graph.setdefault(str(left), []).append((str(right), left < right))
    for node in list(nodes):
        match = re.fullmatch(r"(.+?)-([1-9]\d*)", node)
        if match:
            graph.setdefault(node, []).append((match.group(1), True))
    stack = [(start, False)]
    seen: set[tuple[str, bool]] = set()
    while stack:
        node, has_strict = stack.pop()
        if (node, has_strict) in seen:
            continue
        seen.add((node, has_strict))
        if node == finish and (has_strict or not strict_required):
            return True
        for next_node, strict in graph.get(node, []):
            stack.append((next_node, has_strict or strict))
    return False


def _bounds_candidates(
    function: Any,
    parsed: ParsedSource,
    source_path: str,
    name: str,
    hint: VulnerabilityHint,
    completeness: str,
    api_models: ApiModelConfig,
) -> Iterable[ConstraintCandidate]:
    extents, types, parameters = _declaration_facts(function, parsed)
    input_derived = _input_derived_names(function, parsed, parameters)
    signature_extents = _signature_buffer_extents(function, parsed)
    inferred_extents = set(signature_extents)
    extents.update({key: value for key, value in signature_extents.items() if key not in extents})
    allocated_extents = _allocation_extents(function, parsed, types, api_models)
    extents.update({key: value for key, value in allocated_extents.items() if key not in extents})
    hint_bounds = bool({"bounds_read", "bounds_write"} & set(hint.families))
    for node in descendants(function, "subscript_expression"):
        base_node = node.child_by_field_name("argument") or (node.named_children[0] if node.named_children else None)
        index_node = node.child_by_field_name("index") or (node.named_children[1] if len(node.named_children) > 1 else None)
        if base_node is None or index_node is None:
            continue
        base = value_to_ir(base_node, parsed)
        index = value_to_ir(index_node, parsed)
        index_ids = set(_identifiers(index_node, parsed))
        literal_index = _literal_int(render_value(index))
        if literal_index is None and not index_ids.intersection(input_derived):
            continue
        base_name = _base_identifier(base)
        extent = extents.get(base_name)
        if not extent:
            continue
        index_text = render_value(index)
        direct_name = _base_identifier(index)
        direct_type = types.get(direct_name, "").lower()
        unsigned_index = "unsigned" in direct_type or "size_t" in direct_type or "uint" in direct_type
        offset_match = re.fullmatch(r"([A-Za-z_]\w*)\s*-\s*([1-9]\d*)", index_text)
        if offset_match and any(token in types.get(offset_match.group(1), "").lower() for token in ("unsigned", "size_t", "uint")):
            safe = and_expr((
                Compare(offset_match.group(1), ">=", offset_match.group(2)),
                Compare(offset_match.group(1), "<", extent),
            ))
        elif unsigned_index:
            safe = Compare(index, "<", extent)
        else:
            safe = RangePredicate(index, "0", extent)
        if _is_guarded(node, parsed, safe):
            continue
        violation = or_expr((Compare(index, "<", "0"), Compare(index, ">=", extent)))
        mode = _access_mode(node)
        structural_extent = base_name in inferred_extents
        role = "trigger" if hint_bounds else "hazard"
        yield _candidate(
            parsed=parsed,
            node=node,
            function=name,
            source_path=source_path,
            expr=violation,
            safe_expr=safe,
            role=role,
            gate_type="bounds_gate",
            origin="sink_array_access",
            access_mode=mode,
            hint_match=hint_bounds,
            proven=not structural_extent,
            completeness=completeness,
            tags=("bounds", f"{mode}_access", "signature_extent" if structural_extent else "fixed_extent"),
            description=f"Potential {mode} outside source-backed extent {base_name}[{extent}] at {source_path}:{parsed.span(node).start_line}",
        )

    for node in descendants(function, "pointer_expression"):
        if not _node_text(parsed, node).startswith("*"):
            continue
        argument = node.child_by_field_name("argument")
        argument = unwrap_condition(argument)
        if argument is None or argument.type != "binary_expression":
            continue
        operator_node = argument.child_by_field_name("operator")
        if _node_text(parsed, operator_node) not in {"+", "-"}:
            continue
        base_node = argument.child_by_field_name("left")
        offset_node = argument.child_by_field_name("right")
        if base_node is None or offset_node is None:
            continue
        base = value_to_ir(base_node, parsed)
        offset = value_to_ir(offset_node, parsed)
        offset_ids = set(_identifiers(offset_node, parsed))
        literal_offset = _literal_int(render_value(offset))
        if literal_offset is None and not offset_ids.intersection(input_derived):
            continue
        base_name = _base_identifier(base)
        extent = extents.get(base_name)
        if not extent:
            continue
        safe = RangePredicate(offset, "0", extent)
        if _is_guarded(node, parsed, safe):
            continue
        violation = or_expr((Compare(offset, "<", "0"), Compare(offset, ">=", extent)))
        structural_extent = base_name in inferred_extents
        yield _candidate(
            parsed=parsed,
            node=node,
            function=name,
            source_path=source_path,
            expr=violation,
            safe_expr=safe,
            role="trigger" if hint_bounds else "hazard",
            gate_type="bounds_gate",
            origin="sink_pointer_offset",
            access_mode=_access_mode(node),
            hint_match=hint_bounds,
            proven=not structural_extent,
            completeness=completeness,
            tags=("bounds", "pointer_offset", "signature_extent" if structural_extent else "source_extent"),
            description=f"Pointer offset may escape source-backed extent {base_name}[{extent}]",
        )

    explicit_pointer_limits: dict[str, tuple[Any, Any]] = {}
    for comparison in descendants(function, "binary_expression"):
        operator_node = comparison.child_by_field_name("operator")
        if _node_text(parsed, operator_node) not in {"<", "<="}:
            continue
        left = comparison.child_by_field_name("left")
        right = comparison.child_by_field_name("right")
        if left is None or right is None:
            continue
        left_text = _node_text(parsed, left)
        if left.type in {"identifier", "field_expression"} and right.type in {"identifier", "field_expression"}:
            explicit_pointer_limits[left_text] = (left, right)
    for node in descendants(function, "pointer_expression"):
        if not _node_text(parsed, node).startswith("*"):
            continue
        argument = unwrap_condition(node.child_by_field_name("argument"))
        if argument is None or argument.type not in {"identifier", "field_expression"}:
            continue
        cursor = _node_text(parsed, argument)
        relation = explicit_pointer_limits.get(cursor)
        if relation is None:
            continue
        end = value_to_ir(relation[1], parsed)
        safe = Compare(value_to_ir(argument, parsed), "<", end)
        if _is_guarded(node, parsed, safe):
            continue
        violation = Compare(value_to_ir(argument, parsed), ">=", end)
        yield _candidate(
            parsed=parsed,
            node=node,
            function=name,
            source_path=source_path,
            expr=violation,
            safe_expr=safe,
            role="hazard",
            gate_type="bounds_gate",
            origin="sink_cursor_end_relation",
            access_mode=_access_mode(node),
            hint_match=hint_bounds,
            proven=False,
            completeness=completeness,
            tags=("bounds", "cursor_end", "explicit_source_relation"),
            description=f"Dereference of `{cursor}` is not dominated by its source-declared end relation",
        )

    for call in descendants(function, "call_expression"):
        func = callee_leaf(call, parsed)
        args_nodes = call.child_by_field_name("arguments")
        args = list(args_nodes.named_children) if args_nodes is not None else []
        if func in api_models.memory_functions:
            model = api_models.memory_functions[func]
            dest_index, source_index, length_index = model.destination_arg, model.source_arg, model.length_arg
            if dest_index is None:
                continue
            if len(args) <= max(dest_index, length_index):
                continue
            dest = value_to_ir(args[dest_index], parsed)
            dest_name = _base_identifier(dest)
            extent = extents.get(dest_name)
            length = value_to_ir(args[length_index], parsed)
            length_ids = set(_identifiers(args[length_index], parsed))
            literal_length = _literal_int(render_value(length))
            if literal_length is None and not length_ids.intersection(input_derived):
                continue
            if extent:
                if func == "strncat":
                    used = f"strlen({render_value(dest)}) + {render_value(length)} + 1"
                    violation = Compare(used, ">", extent)
                    safe = Compare(used, "<=", extent)
                else:
                    violation = Compare(length, ">", extent)
                    safe = Compare(length, "<=", extent)
                if not _is_guarded(call, parsed, safe):
                    role = "trigger" if hint_bounds else "hazard"
                    yield _candidate(
                        parsed=parsed,
                        node=call,
                        function=name,
                        source_path=source_path,
                        expr=violation,
                        safe_expr=safe,
                        role=role,
                        gate_type="bounds_gate",
                        origin="sink_memory_call",
                        access_mode="write",
                        hint_match=hint_bounds,
                        proven=dest_name not in inferred_extents,
                        completeness=completeness,
                        tags=("bounds", "memory_call", func),
                        description=f"{func} length may exceed destination extent {dest_name}[{extent}]",
                    )
            if source_index is not None and len(args) > source_index:
                source_value = value_to_ir(args[source_index], parsed)
                source_name = _base_identifier(source_value)
                source_extent = extents.get(source_name)
                if source_extent:
                    source_violation = Compare(length, ">", source_extent)
                    source_safe = Compare(length, "<=", source_extent)
                    if not _is_guarded(call, parsed, source_safe):
                        yield _candidate(
                            parsed=parsed,
                            node=call,
                            function=name,
                            source_path=source_path,
                            expr=source_violation,
                            safe_expr=source_safe,
                            role="trigger" if hint_bounds else "hazard",
                            gate_type="bounds_gate",
                            origin="sink_memory_call_source",
                            access_mode="read",
                            hint_match=hint_bounds,
                            proven=source_name not in inferred_extents,
                            completeness=completeness,
                            tags=("bounds", "memory_call", func, "source_extent"),
                            description=f"{func} length may exceed source extent {source_name}[{source_extent}]",
                        )
            if func == "strncpy" and source_index is not None and len(args) > source_index:
                later_string_use = any(
                    later.start_byte > call.end_byte
                    and callee_leaf(later, parsed) in {"strlen", "strcmp", "strcat", "strcpy", "puts"}
                    and dest_name in argument_texts(later, parsed)
                    for later in descendants(function, "call_expression")
                )
                if later_string_use:
                    source_value = value_to_ir(args[source_index], parsed)
                    bounded_length = CallValue("strnlen", (source_value, length))
                    termination_violation = Compare(bounded_length, "==", length)
                    termination_safe = Compare(bounded_length, "<", length)
                    yield _candidate(
                        parsed=parsed,
                        node=call,
                        function=name,
                        source_path=source_path,
                        expr=termination_violation,
                        safe_expr=termination_safe,
                        role="trigger" if "bounds_read" in hint.families else "hazard",
                        gate_type="bounds_gate",
                        origin="sink_string_termination",
                        access_mode="read",
                        hint_match="bounds_read" in hint.families,
                        proven=True,
                        completeness=completeness,
                        tags=("bounds", "string_termination", "strncpy"),
                        description=f"strncpy may leave `{dest_name}` unterminated before a later string read",
                    )
        elif func in _UNBOUNDED_COPY_CALLS and args:
            dest_name = _base_identifier(value_to_ir(args[0], parsed))
            extent = extents.get(dest_name)
            if not extent:
                continue
            expr = AccessPredicate(dest_name, "0", extent, "unknown_copy_width", valid=False)
            yield _candidate(
                parsed=parsed,
                node=call,
                function=name,
                source_path=source_path,
                expr=expr,
                safe_expr=None,
                role="hazard",
                gate_type="bounds_gate",
                origin="sink_unbounded_copy",
                access_mode="write",
                hint_match=hint_bounds,
                proven=False,
                completeness=completeness,
                tags=("bounds", "unbounded_copy", func),
                description=f"{func} writes to fixed destination {dest_name}[{extent}] without an explicit source bound",
            )


def _arithmetic_candidates(
    function: Any,
    parsed: ParsedSource,
    source_path: str,
    name: str,
    hint: VulnerabilityHint,
    completeness: str,
    api_models: ApiModelConfig,
) -> Iterable[ConstraintCandidate]:
    _extents, types, parameters = _declaration_facts(function, parsed)
    hint_arithmetic = "integer_arithmetic" in hint.families
    for node in descendants(function, "binary_expression"):
        operator_node = node.child_by_field_name("operator")
        operator = _node_text(parsed, operator_node)
        left_node = node.child_by_field_name("left")
        right_node = node.child_by_field_name("right")
        if left_node is None or right_node is None:
            continue
        left = value_to_ir(left_node, parsed)
        right = value_to_ir(right_node, parsed)
        if operator in {"/", "%"}:
            literal = _literal_int(render_value(right))
            if literal is not None and literal != 0:
                continue
            safe = Compare(right, "!=", "0")
            if _is_guarded(node, parsed, safe):
                continue
            violation = Compare(right, "==", "0")
            yield _candidate(
                parsed=parsed,
                node=node,
                function=name,
                source_path=source_path,
                expr=violation,
                safe_expr=safe,
                role="trigger" if hint_arithmetic else "hazard",
                gate_type="value_gate",
                origin="sink_division",
                access_mode="arithmetic",
                hint_match=hint_arithmetic,
                proven=True,
                completeness=completeness,
                tags=("arithmetic", "division_by_zero"),
                description=f"Denominator can be zero in `{_node_text(parsed, node)}`",
            )
        elif operator in {"<<", ">>"}:
            right_text = render_value(right)
            literal = _literal_int(right_text)
            left_name = _base_identifier(left)
            width = _type_width(types.get(left_name, ""))
            if literal is not None and width is not None and 0 <= literal < width:
                continue
            if width is not None:
                violation = or_expr((Compare(right, "<", "0"), Compare(right, ">=", str(width))))
                safe = RangePredicate(right, "0", str(width))
                proven = True
            else:
                violation = RangePredicate(right, "0", f"bit_width({render_value(left)})").negate()
                safe = None
                proven = False
            yield _candidate(
                parsed=parsed,
                node=node,
                function=name,
                source_path=source_path,
                expr=violation,
                safe_expr=safe,
                role="trigger" if hint_arithmetic and proven else "hazard",
                gate_type="value_gate",
                origin="sink_shift",
                access_mode="arithmetic",
                hint_match=hint_arithmetic,
                proven=proven,
                completeness=completeness,
                tags=("arithmetic", "shift_range"),
                description=f"Shift count may be outside the operand width in `{_node_text(parsed, node)}`",
            )
        elif operator in {"+", "*"}:
            identifiers = set(_identifiers(node, parsed))
            if not identifiers.intersection(parameters):
                continue
            parent_text = _node_text(parsed, node.parent)
            used_by_size_sink = any(token in parent_text for token in ("malloc", "calloc", "realloc", "[", "memcpy", "memmove"))
            if not used_by_size_sink and not hint_arithmetic:
                continue
            expr = OverflowPredicate(value_to_ir(node, parsed), "size_t", overflows=True)
            yield _candidate(
                parsed=parsed,
                node=node,
                function=name,
                source_path=source_path,
                expr=expr,
                safe_expr=OverflowPredicate(value_to_ir(node, parsed), "size_t", overflows=False),
                role="trigger" if hint_arithmetic and used_by_size_sink else "hazard",
                gate_type="bounds_gate",
                origin="sink_size_arithmetic",
                access_mode="arithmetic",
                hint_match=hint_arithmetic,
                proven=used_by_size_sink,
                completeness=completeness,
                tags=("arithmetic", "size_overflow", "input_derived"),
                description=f"Input-derived size arithmetic may overflow: `{_node_text(parsed, node)}`",
            )

    for cast in descendants(function, "cast_expression"):
        type_node = cast.child_by_field_name("type")
        value_node = cast.child_by_field_name("value")
        if type_node is None or value_node is None:
            continue
        target_type = _node_text(parsed, type_node)
        bounds = _fixed_integer_range(target_type)
        if bounds is None:
            continue
        value = value_to_ir(value_node, parsed)
        literal = _literal_int(render_value(value))
        lower, upper = bounds
        if literal is not None and lower <= literal < upper:
            continue
        safe = RangePredicate(value, str(lower), str(upper))
        if _is_guarded(cast, parsed, safe):
            continue
        violation = or_expr((Compare(value, "<", str(lower)), Compare(value, ">=", str(upper))))
        yield _candidate(
            parsed=parsed,
            node=cast,
            function=name,
            source_path=source_path,
            expr=violation,
            safe_expr=safe,
            role="trigger" if hint_arithmetic else "hazard",
            gate_type="value_gate",
            origin="sink_integer_truncation",
            access_mode="arithmetic",
            hint_match=hint_arithmetic,
            proven=True,
            completeness=completeness,
            tags=("arithmetic", "integer_conversion", target_type),
            description=f"Conversion to {target_type} truncates values outside [{lower}, {upper})",
        )

    size_arguments = dict(api_models.allocation_functions)
    size_arguments.update({name: (model.length_arg,) for name, model in api_models.memory_functions.items()})
    for call in descendants(function, "call_expression"):
        positions = size_arguments.get(callee_leaf(call, parsed))
        arguments_node = call.child_by_field_name("arguments")
        arguments = list(arguments_node.named_children) if arguments_node is not None else []
        if not positions:
            continue
        for position in positions:
            if position >= len(arguments):
                continue
            argument = arguments[position]
            identifiers = _identifiers(argument, parsed)
            if len(identifiers) != 1:
                continue
            variable = identifiers[0]
            type_name = types.get(variable, "").lower()
            if not type_name or "unsigned" in type_name or "uint" in type_name or "size_t" in type_name:
                continue
            violation = Compare(variable, "<", "0")
            safe = Compare(variable, ">=", "0")
            if _is_guarded(call, parsed, safe):
                continue
            yield _candidate(
                parsed=parsed,
                node=argument,
                function=name,
                source_path=source_path,
                expr=violation,
                safe_expr=safe,
                role="trigger" if hint_arithmetic else "hazard",
                gate_type="bounds_gate",
                origin="sink_negative_size_conversion",
                access_mode="arithmetic",
                hint_match=hint_arithmetic,
                proven=True,
                completeness=completeness,
                tags=("arithmetic", "signed_to_size", callee_leaf(call, parsed)),
                description=f"Signed `{variable}` can become a large size argument to {callee_leaf(call, parsed)}",
            )


def _pointer_assignments(
    function: Any, parsed: ParsedSource, api_models: ApiModelConfig,
) -> dict[str, tuple[Any, str]]:
    assignments: dict[str, tuple[Any, str]] = {}
    for init in descendants(function, "init_declarator"):
        declarator = init.child_by_field_name("declarator")
        value = init.child_by_field_name("value")
        ids = list(descendants(declarator, "identifier")) if declarator is not None else []
        if not ids or value is None:
            continue
        calls = list(descendants(value, "call_expression"))
        if calls and callee_leaf(calls[0], parsed) in api_models.nullable_returns:
            assignments[_node_text(parsed, ids[-1])] = (init, callee_leaf(calls[0], parsed))
    return assignments


def _top_statement(node: Any) -> Any:
    current = node
    while current.parent is not None and current.parent.type != "compound_statement":
        current = current.parent
    return current


def _same_compound_sequence(first: Any, second: Any) -> bool:
    first_statement = _top_statement(first)
    second_statement = _top_statement(second)
    return (
        first_statement.parent is not None
        and second_statement.parent is not None
        and first_statement.parent.start_byte == second_statement.parent.start_byte
        and first_statement.parent.end_byte == second_statement.parent.end_byte
        and first_statement.start_byte < second_statement.start_byte
    )


def _assigned_names(node: Any, parsed: ParsedSource) -> set[str]:
    names: set[str] = set()
    if node is None:
        return names
    for assignment in descendants(node, "assignment_expression"):
        left = assignment.child_by_field_name("left")
        name = _assigned_scalar_name(left, parsed)
        if name:
            names.add(name)
    return names


def _switch_assigned_on_all_paths(node: Any, parsed: ParsedSource) -> tuple[set[str], set[str]]:
    cases = list(descendants(node, "case_statement"))
    if not cases:
        return set(), set()
    has_default = any(case.child_by_field_name("value") is None for case in cases)
    per_case = [_assigned_names(case, parsed) for case in cases]
    maybe = set().union(*per_case)
    definite = set.intersection(*per_case) if has_default and per_case else set()
    return definite, maybe


def _allocation_extents(
    function: Any, parsed: ParsedSource, types: dict[str, str], api_models: ApiModelConfig,
) -> dict[str, str]:
    extents: dict[str, str] = {}
    for init in descendants(function, "init_declarator"):
        declarator = init.child_by_field_name("declarator")
        value = init.child_by_field_name("value")
        identifiers = list(descendants(declarator, "identifier")) if declarator is not None else []
        calls = list(descendants(value, "call_expression")) if value is not None else []
        if not identifiers or not calls:
            continue
        call = calls[0]
        func = callee_leaf(call, parsed)
        args = argument_texts(call, parsed)
        extent = ""
        variable = _node_text(parsed, identifiers[-1])
        byte_addressable = any(token in types.get(variable, "").lower() for token in ("char", "int8_t", "uint8_t", "byte"))
        positions = api_models.allocation_functions.get(func, ())
        selected_args = [args[index] for index in positions if index < len(args)]
        if len(selected_args) == 1:
            size = selected_args[0]
            count_match = re.match(r"\s*(.+?)\s*\*\s*sizeof\s*\(", size)
            extent = count_match.group(1).strip(" ()") if count_match else size if byte_addressable else ""
        elif len(selected_args) == 2:
            extent = (
                selected_args[0]
                if "sizeof" in selected_args[1]
                else f"({selected_args[0]}) * ({selected_args[1]})" if byte_addressable else ""
            )
        if extent:
            extents[variable] = extent
    return extents


def _null_lifetime_candidates(
    function: Any,
    parsed: ParsedSource,
    source_path: str,
    name: str,
    hint: VulnerabilityHint,
    completeness: str,
    api_models: ApiModelConfig,
) -> Iterable[ConstraintCandidate]:
    pointer_assignments = _pointer_assignments(function, parsed, api_models)
    hint_null = "null_return" in hint.families
    hint_lifetime = "lifetime" in hint.families
    aliases: dict[str, str] = {}

    def canonical(variable: str) -> str:
        seen: set[str] = set()
        while variable in aliases and variable not in seen:
            seen.add(variable)
            variable = aliases[variable]
        return variable

    for init in descendants(function, "init_declarator"):
        declarator = init.child_by_field_name("declarator")
        value = init.child_by_field_name("value")
        left_ids = list(descendants(declarator, "identifier")) if declarator is not None else []
        right_ids = list(descendants(value, "identifier")) if value is not None else []
        if left_ids and len(right_ids) == 1 and not list(descendants(value, "call_expression")):
            aliases[_node_text(parsed, left_ids[-1])] = _node_text(parsed, right_ids[-1])
    frees: dict[str, list[Any]] = {}
    for call in descendants(function, "call_expression"):
        func = callee_leaf(call, parsed)
        args = argument_texts(call, parsed)
        if func in api_models.deallocation_functions and len(args) > api_models.deallocation_functions[func]:
            variable = args[api_models.deallocation_functions[func]].strip()
            frees.setdefault(canonical(variable), []).append(call)
    for delete in descendants(function, "delete_expression"):
        ids = list(descendants(delete, "identifier"))
        if ids:
            frees.setdefault(canonical(_node_text(parsed, ids[-1])), []).append(delete)

    for variable, (assignment, allocator) in pointer_assignments.items():
        for node in walk(function):
            if node.start_byte <= assignment.end_byte:
                continue
            is_use = (
                node.type == "field_expression" and "->" in _node_text(parsed, node)
                or node.type == "subscript_expression"
                or node.type == "pointer_expression" and _node_text(parsed, node).startswith("*")
            )
            if not is_use or variable not in _identifiers(node, parsed):
                continue
            safe = Compare(variable, "!=", "NULL")
            if _is_guarded(node, parsed, safe):
                break
            yield _candidate(
                parsed=parsed,
                node=node,
                function=name,
                source_path=source_path,
                expr=Compare(variable, "==", "NULL"),
                safe_expr=safe,
                role="trigger" if hint_null else "hazard",
                gate_type="value_gate",
                origin="sink_nullable_use",
                access_mode="read",
                hint_match=hint_null,
                proven=True,
                completeness=completeness,
                tags=("null", "allocator_result", allocator),
                description=f"Result of {allocator} may be used through `{variable}` without a dominating null check",
            )
            break

    for init in descendants(function, "init_declarator"):
        declarator = init.child_by_field_name("declarator")
        value = init.child_by_field_name("value")
        ids = list(descendants(declarator, "identifier")) if declarator is not None else []
        calls = list(descendants(value, "call_expression")) if value is not None else []
        if not ids or not calls:
            continue
        api_name = callee_leaf(calls[0], parsed)
        failure_values = api_models.failure_returns.get(api_name)
        if not failure_values:
            continue
        variable = _node_text(parsed, ids[-1])
        dangerous_use = None
        for later in descendants(function, "call_expression"):
            if later.start_byte <= init.end_byte:
                continue
            model = api_models.memory_functions.get(callee_leaf(later, parsed))
            args = argument_texts(later, parsed)
            if model is not None and model.length_arg < len(args) and variable in _identifiers(later, parsed):
                dangerous_use = later
                break
        if dangerous_use is None:
            continue
        violations = [Compare(variable, "==", failure) for failure in failure_values]
        violation = or_expr(violations)
        safe = and_expr(Compare(variable, "!=", failure) for failure in failure_values)
        if _is_guarded(dangerous_use, parsed, safe):
            continue
        yield _candidate(
            parsed=parsed,
            node=dangerous_use,
            function=name,
            source_path=source_path,
            expr=violation,
            safe_expr=safe,
            role="trigger" if hint_null else "hazard",
            gate_type="value_gate",
            origin="sink_failure_return_use",
            access_mode="return_value",
            hint_match=hint_null,
            proven=True,
            completeness=completeness,
            tags=("return", "failure_code", api_name),
            description=f"Failure return from {api_name} reaches a memory-operation length without a dominating check",
        )

    for variable, free_calls in frees.items():
        free_calls.sort(key=lambda item: item.start_byte)
        if len(free_calls) > 1:
            second = free_calls[1]
            linear = _same_compound_sequence(free_calls[0], second)
            yield _candidate(
                parsed=parsed,
                node=second,
                function=name,
                source_path=source_path,
                expr=AlivePredicate(variable, alive=False),
                safe_expr=AlivePredicate(variable, alive=True),
                role="trigger" if hint_lifetime and linear else "hazard",
                gate_type="value_gate",
                origin="sink_double_free",
                access_mode="free",
                hint_match=hint_lifetime,
                proven=linear,
                completeness=completeness,
                tags=("lifetime", "double_free"),
                description=f"`{variable}` is freed more than once on the lexical path",
            )
        first = free_calls[0]
        alias_group = {name for name in {variable, *aliases} if canonical(name) == variable}
        later_ids = [
            item for item in descendants(function, "identifier")
            if item.start_byte > first.end_byte and _node_text(parsed, item) in alias_group
        ]
        for ident in later_ids:
            if any(call.start_byte <= ident.start_byte <= call.end_byte for call in free_calls):
                continue
            yield _candidate(
                parsed=parsed,
                node=ident,
                function=name,
                source_path=source_path,
                expr=AlivePredicate(variable, alive=False),
                safe_expr=AlivePredicate(variable, alive=True),
                role="hazard",
                gate_type="value_gate",
                origin="sink_use_after_free",
                access_mode="read",
                hint_match=hint_lifetime,
                proven=False,
                completeness=completeness,
                tags=("lifetime", "use_after_free", "lexical_alias_only"),
                description=f"`{variable}` is referenced after free; branch/alias validation is still required",
            )
            break

    for ret in descendants(function, "return_statement"):
        text = _node_text(parsed, ret)
        match = re.search(r"return\s+&\s*([A-Za-z_]\w*)", text)
        if not match:
            continue
        variable = match.group(1)
        yield _candidate(
            parsed=parsed,
            node=ret,
            function=name,
            source_path=source_path,
            expr=AlivePredicate(variable, alive=False),
            safe_expr=AlivePredicate(variable, alive=True),
            role="trigger" if hint_lifetime else "hazard",
            gate_type="value_gate",
            origin="sink_stack_escape",
            access_mode="escape",
            hint_match=hint_lifetime,
            proven=True,
            completeness=completeness,
            tags=("lifetime", "stack_escape"),
            description=f"Address of local `{variable}` escapes its lifetime",
        )

    local_names: set[str] = set()
    for declaration in descendants(function, "declaration"):
        for identifier in descendants(declaration, "identifier"):
            local_names.add(_node_text(parsed, identifier))
    for pointer in descendants(function, "pointer_expression"):
        if not _node_text(parsed, pointer).startswith("&"):
            continue
        ids = list(descendants(pointer, "identifier"))
        if not ids:
            continue
        variable = _node_text(parsed, ids[-1])
        if variable not in local_names:
            continue
        parent = pointer.parent
        while parent is not None and parent.type not in {"assignment_expression", "return_statement", "function_definition"}:
            parent = parent.parent
        if parent is None or parent.type != "assignment_expression":
            continue
        left = parent.child_by_field_name("left")
        left_text = _node_text(parsed, left)
        if left_text in local_names:
            continue
        yield _candidate(
            parsed=parsed,
            node=parent,
            function=name,
            source_path=source_path,
            expr=AlivePredicate(variable, alive=False),
            safe_expr=AlivePredicate(variable, alive=True),
            role="hazard",
            gate_type="value_gate",
            origin="sink_stack_escape_assignment",
            access_mode="escape",
            hint_match=hint_lifetime,
            proven=False,
            completeness=completeness,
            tags=("lifetime", "stack_escape", "simple_alias"),
            description=f"Address of local `{variable}` escapes through `{left_text}`",
        )


def _linear_uninitialized_candidates(
    function: Any,
    parsed: ParsedSource,
    source_path: str,
    name: str,
    hint: VulnerabilityHint,
    completeness: str,
    api_models: ApiModelConfig,
) -> Iterable[ConstraintCandidate]:
    if "initialization" not in hint.families:
        return
    body = function.child_by_field_name("body")
    if body is None:
        return
    declared: dict[str, Any] = {}
    initialized: set[str] = set()
    maybe_initialized: set[str] = set()
    for statement in body.named_children:
        if statement.type == "declaration":
            for init in descendants(statement, "init_declarator"):
                declarator = init.child_by_field_name("declarator")
                ids = list(descendants(declarator, "identifier")) if declarator is not None else []
                if ids:
                    initialized.add(_node_text(parsed, ids[-1]))
            for ident in descendants(statement, "identifier"):
                variable = _node_text(parsed, ident)
                declared.setdefault(variable, statement)
            continue
        if statement.type == "expression_statement":
            for assignment in descendants(statement, "assignment_expression"):
                left = assignment.child_by_field_name("left")
                variable = _assigned_scalar_name(left, parsed)
                if variable:
                    initialized.add(variable)
                    maybe_initialized.add(variable)
            for call in descendants(statement, "call_expression"):
                leaf = callee_leaf(call, parsed)
                args = argument_texts(call, parsed)
                if leaf == "memset" and len(args) >= 3:
                    variable = args[0].lstrip("&*").strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", variable):
                        initialized.add(variable)
                        maybe_initialized.add(variable)
                read_destination = {"read": 1, "recv": 1, "fread": 0}.get(leaf)
                if read_destination is not None and read_destination < len(args):
                    variable = args[read_destination].lstrip("&*").strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", variable):
                        maybe_initialized.add(variable)
        if statement.type == "if_statement":
            consequence = statement.child_by_field_name("consequence")
            alternative = statement.child_by_field_name("alternative")
            left = _assigned_names(consequence, parsed)
            right = _assigned_names(alternative, parsed) if alternative is not None else set()
            initialized.update(left & right)
            maybe_initialized.update(left | right)
        if statement.type == "switch_statement":
            definite, maybe = _switch_assigned_on_all_paths(statement, parsed)
            initialized.update(definite)
            maybe_initialized.update(maybe)
        for ident in descendants(statement, "identifier"):
            variable = _node_text(parsed, ident)
            if variable not in declared or variable in initialized:
                continue
            parent = ident.parent
            if parent is not None and parent.type == "pointer_expression" and _node_text(parsed, parent).startswith("&"):
                continue
            if parent is not None and parent.type in {"assignment_expression", "update_expression"}:
                left = parent.child_by_field_name("left")
                if left is not None and left.start_byte <= ident.start_byte <= left.end_byte:
                    continue
            proven = variable not in maybe_initialized
            yield _candidate(
                parsed=parsed,
                node=ident,
                function=name,
                source_path=source_path,
                expr=InitializedPredicate(variable, initialized=False),
                safe_expr=InitializedPredicate(variable, initialized=True),
                role="trigger" if proven else "hazard",
                gate_type="value_gate",
                origin="sink_uninitialized_read",
                access_mode="read",
                hint_match=True,
                proven=proven,
                completeness=completeness,
                tags=("initialization", "linear_def_use"),
                description=f"`{variable}` may be read before definite top-level initialization",
            )
            initialized.add(variable)  # one diagnostic per variable


def _resource_state_candidates(
    function: Any,
    parsed: ParsedSource,
    source_path: str,
    name: str,
    hint: VulnerabilityHint,
    completeness: str,
    api_models: ApiModelConfig,
) -> Iterable[ConstraintCandidate]:
    families = set(hint.families)
    if "resource_progress" in families:
        _extents, _types, parameters = _declaration_facts(function, parsed)
        for call in descendants(function, "call_expression"):
            func = callee_leaf(call, parsed)
            if func not in api_models.allocation_functions:
                continue
            args = argument_texts(call, parsed)
            if not args or not set(_identifiers(call, parsed)).intersection(parameters):
                continue
            expr = UnknownPredicate(f"allocation_size({', '.join(args)}) is input-controlled", "resource_bound")
            yield _candidate(
                parsed=parsed,
                node=call,
                function=name,
                source_path=source_path,
                expr=expr,
                safe_expr=None,
                role="hazard",
                gate_type="bounds_gate",
                origin="sink_input_allocation",
                access_mode="allocation",
                hint_match=True,
                proven=False,
                completeness=completeness,
                tags=("resource", "allocation", "input_derived"),
                description=f"Input-derived allocation size reaches {func}; no source bound was proven",
            )
        for loop_type in ("while_statement", "for_statement"):
            for loop in descendants(function, loop_type):
                condition = loop.child_by_field_name("condition")
                if condition is None:
                    continue
                condition_ids = set(_identifiers(condition, parsed))
                if not condition_ids.intersection(parameters):
                    continue
                update = loop.child_by_field_name("update")
                body = loop.child_by_field_name("body")
                changed = set(_identifiers(update, parsed)) if update is not None else set()
                if body is not None:
                    for assignment in descendants(body, "assignment_expression"):
                        left = assignment.child_by_field_name("left")
                        changed.update(_identifiers(left, parsed) if left is not None else [])
                    for update_expr in descendants(body, "update_expression"):
                        changed.update(_identifiers(update_expr, parsed))
                if condition_ids.intersection(changed):
                    continue
                stagnant = sorted(condition_ids)
                expr = ProgressPredicate(", ".join(stagnant), progresses=False)
                yield _candidate(
                    parsed=parsed,
                    node=loop,
                    function=name,
                    source_path=source_path,
                    expr=expr,
                    safe_expr=None,
                    role="hazard",
                    gate_type="path_gate",
                    origin="sink_progress",
                    access_mode="loop",
                    hint_match=True,
                    proven=False,
                    completeness=completeness,
                    tags=("resource", "progress", "input_derived"),
                    description=f"Input-derived loop condition may not update: {', '.join(stagnant)}",
                )
    if "state_semantic" in families:
        calls = list(descendants(function, "call_expression"))
        mutation_calls = [
            call for call in calls
            if any(token in callee_leaf(call, parsed).lower() for token in ("set", "insert", "add", "push", "append", "del", "remove"))
        ]
        for first, second in zip(mutation_calls, mutation_calls[1:]):
            if second.start_byte - first.end_byte > 2000:
                continue
            between = parsed.source[first.end_byte:second.start_byte].decode("utf-8", errors="replace")
            if "return" not in between and "goto" not in between:
                continue
            expr = InitializedPredicate(f"state_after({_node_text(parsed, first)})", initialized=False)
            yield _candidate(
                parsed=parsed,
                node=second,
                function=name,
                source_path=source_path,
                expr=expr,
                safe_expr=None,
                role="hazard",
                gate_type="value_gate",
                origin="sink_state_consistency",
                access_mode="state",
                hint_match=True,
                proven=False,
                completeness=completeness,
                tags=("state", "paired_mutation", "manual_validation_required"),
                description="State mutation sequence has an intervening exit path; consistency requires review",
            )
            break


def analyze_sinks(
    parsed: ParsedSource,
    *,
    source_path: str,
    sink_function: str,
    sink_span: Any,
    hint: VulnerabilityHint,
    budget: AnalysisBudget,
    completeness: str,
    api_models: ApiModelConfig,
    budget_context: Optional[BudgetContext] = None,
) -> SinkAnalysis:
    diagnostics: list[ExtractionDiagnostic] = []
    functions = _function_nodes(parsed, sink_function, hint)
    if not functions:
        diagnostics.append(ExtractionDiagnostic(
            "sink_unresolved",
            f"No unique source function matched sink {sink_function or hint.symbols!r}",
            "warning",
        ))
        return SinkAnalysis([], diagnostics, False, 0)
    if len(functions) > 1:
        diagnostics.append(ExtractionDiagnostic(
            "sink_ambiguous",
            f"Sink name matched {len(functions)} source definitions; overload resolution is not available",
            "warning",
        ))

    candidates: list[ConstraintCandidate] = []
    truncated = False
    for function in functions:
        name = function_name(function, parsed) or sink_function
        families = set(hint.families)
        semantic_families = families - {"format_routing"}
        detectors = []
        if not semantic_families or {"bounds_read", "bounds_write"} & families:
            detectors.append(_bounds_candidates)
        if not semantic_families or "integer_arithmetic" in families:
            detectors.append(_arithmetic_candidates)
        if not semantic_families or {"null_return", "lifetime"} & families:
            detectors.append(_null_lifetime_candidates)
        if "initialization" in families:
            detectors.append(_linear_uninitialized_candidates)
        if {"resource_progress", "state_semantic"} & families:
            detectors.append(_resource_state_candidates)
        for detector in detectors:
            for candidate in detector(function, parsed, source_path, name, hint, completeness, api_models):
                if budget_context is not None:
                    try:
                        budget_context.consume_candidate(f"sink:{detector.__name__}")
                    except BudgetExceeded as exc:
                        diagnostics.append(ExtractionDiagnostic(
                            "analysis_budget_exhausted", exc.reason, "warning", candidate.source_span,
                        ))
                        truncated = True
                        break
                if sink_span is not None:
                    requested_line = (
                        sink_span if isinstance(sink_span, int)
                        else sink_span.get("start_line", sink_span.get("line")) if isinstance(sink_span, dict)
                        else getattr(sink_span, "start_line", None)
                    )
                    if requested_line and abs(candidate.source_span.start_line - int(requested_line)) > 80:
                        continue
                candidates.append(candidate)
                if len(candidates) >= budget.max_sink_candidates:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    # Exact source-span/formula dedup only; alternative hazards stay separate.
    unique: dict[tuple[Any, ...], ConstraintCandidate] = {}
    for item in candidates:
        span = item.source_span
        key = (
            item.role,
            item.origin,
            span.start_byte if span else -1,
            span.end_byte if span else -1,
            item.normalized_formula,
        )
        unique[key] = item
    candidates = sorted(
        unique.values(),
        key=lambda item: (
            -item.confidence_score,
            item.source_span.start_byte if item.source_span else -1,
            item.origin,
        ),
    )
    if truncated:
        diagnostics.append(ExtractionDiagnostic(
            "sink_budget_exhausted",
            f"Sink candidate budget {budget.max_sink_candidates} exhausted",
            "warning",
        ))
    if completeness == "snippet":
        diagnostics.append(ExtractionDiagnostic(
            "incomplete_source",
            "Sink analysis used a snippet; candidates cannot be high confidence",
            "warning",
        ))
    return SinkAnalysis(candidates, diagnostics, True, len(functions), truncated)


__all__ = ["SinkAnalysis", "analyze_sinks"]
