"""Finite, source-backed argument and fuzzer-input mappings.

This is intentionally not SSA.  It records only exact syntax-level bindings
at resolved callsites so later reasoning can relate caller expressions to
callee parameters without inventing aliases.
"""

from __future__ import annotations

from typing import Any, Iterable

from .ast import (
    ParsedSource,
    call_matches,
    callee_leaf,
    descendants,
    enclosing_function,
    function_matches,
    function_name,
    parse_source,
    target_span_matches,
    value_to_ir,
)
from .ir import Compare, UnknownPredicate, and_expr, expr_to_dict
from .models import BudgetContext, BudgetExceeded, ConstraintCandidate, ExtractionRequest, stable_path_id


def _parameter_names(function: Any, parsed: ParsedSource) -> list[str]:
    declarator = function.child_by_field_name("declarator")
    if declarator is None:
        return []
    names: list[str] = []
    for parameter in descendants(declarator, "parameter_declaration"):
        identifiers = list(descendants(parameter, "identifier"))
        if identifiers:
            names.append(parsed.text(identifiers[-1]).strip())
    return names


def _definitions(parsed: ParsedSource, target: str) -> Iterable[tuple[Any, ParsedSource]]:
    for function in descendants(parsed.root, "function_definition"):
        if function_matches(function_name(function, parsed), target):
            yield function, parsed


def _binding_candidate(
    parsed: ParsedSource,
    call: Any,
    caller: str,
    target: str,
    parameter: str,
    argument: Any,
    index: int,
    source_path: str,
    completeness: str,
) -> ConstraintCandidate:
    expr = Compare(parameter, "==", value_to_ir(argument, parsed))
    span = parsed.span(argument)
    confidence = "low" if completeness == "snippet" or parsed.local_has_error(call) else "high"
    score = 0.35 if confidence == "low" else 0.9
    formula = expr.render()
    return ConstraintCandidate(
        gate_type="value_gate",
        description=f"Bind {target} parameter `{parameter}` to caller argument {index}: `{parsed.text(argument).strip()}`",
        required_condition=formula,
        polarity="satisfy",
        confidence=confidence,
        confidence_score=score,
        source=source_path,
        node_function=caller,
        enclosing_function=caller,
        target_function=target,
        target_call_span=parsed.span(call),
        source_span=span,
        sink_span=span,
        normalized_formula=formula,
        required_formula=formula,
        raw_condition=parsed.text(argument).strip(),
        structured_formula=expr_to_dict(expr),
        role="dataflow",
        path_id=stable_path_id("edge", source_path, caller, target, parsed.span(call)),
        origin="caller_argument_binding",
        control_origin="finite_def_use",
        promotable=False,
        confidence_reasons=["exact callsite argument", "source callee parameter declaration"],
        symbol_dependencies=[parameter],
        semantic_tags=["dataflow", "argument_binding"],
    )


def analyze_argument_bindings(
    parsed: ParsedSource,
    request: ExtractionRequest,
    budget_context: BudgetContext | None = None,
) -> tuple[list[ConstraintCandidate], list[str]]:
    """Return exact caller-argument to callee-parameter equations."""
    if not request.target_function:
        return [], []
    definitions = list(_definitions(parsed, request.target_function))
    diagnostics: list[str] = []
    for index, (path, unit) in enumerate(request.related_sources.items()):
        if index >= request.budget.max_related_sources:
            diagnostics.append("related_source_budget_exhausted")
            break
        if budget_context is not None:
            try:
                budget_context.checkpoint("related_sources")
            except BudgetExceeded:
                diagnostics.append("related_source_deadline_exhausted")
                break
            budget_context.related_sources += 1
        related = parse_source(
            unit.text,
            file_extension=unit.file_extension,
            language=unit.language,
            line_offset=unit.line_offset,
            preferred_symbols=(request.target_function,),
        )
        if related is None:
            diagnostics.append(f"related_source_parse_failed:{path}")
            continue
        definitions.extend(_definitions(related, request.target_function))
    if len(definitions) != 1:
        if definitions:
            diagnostics.append("callee_definition_ambiguous")
        return [], diagnostics
    function, definition_source = definitions[0]
    parameters = _parameter_names(function, definition_source)
    if not parameters:
        return [], diagnostics

    candidates: list[ConstraintCandidate] = []
    for call in descendants(parsed.root, "call_expression"):
        if not call_matches(call, request.target_function, parsed):
            continue
        if not target_span_matches(call, parsed, request.target_callsite):
            continue
        _function, actual_caller = enclosing_function(call, parsed)
        if request.caller_function and actual_caller and not function_matches(actual_caller, request.caller_function):
            continue
        arguments_node = call.child_by_field_name("arguments")
        arguments = list(arguments_node.named_children) if arguments_node is not None else []
        for index, (parameter, argument) in enumerate(zip(parameters, arguments)):
            candidates.append(_binding_candidate(
                parsed,
                call,
                request.caller_function,
                request.target_function,
                parameter,
                argument,
                index,
                request.source.path,
                request.source.completeness,
            ))
    return candidates, diagnostics


def fuzzer_input_symbols(parsed: ParsedSource) -> dict[str, tuple[str, ...]]:
    """Map recognized harness entry functions to their source parameter names."""
    mappings: dict[str, tuple[str, ...]] = {}
    for function in descendants(parsed.root, "function_definition"):
        name = function_name(function, parsed)
        if name != "LLVMFuzzerTestOneInput":
            continue
        parameters = _parameter_names(function, parsed)
        if len(parameters) >= 2:
            mappings[name] = tuple(parameters[:2])
    return mappings


def _dataflow_candidate(
    parsed: ParsedSource,
    node: Any,
    request: ExtractionRequest,
    formula: Any,
    origin: str,
    description: str,
    symbols: list[str],
) -> ConstraintCandidate:
    span = parsed.span(node)
    rendered = formula.render()
    function = "LLVMFuzzerTestOneInput"
    confidence = "low" if request.source.completeness == "snippet" or parsed.local_has_error(node) else "high"
    return ConstraintCandidate(
        gate_type="value_gate",
        description=description,
        required_condition=rendered,
        polarity="satisfy",
        confidence=confidence,
        confidence_score=0.35 if confidence == "low" else 0.9,
        source=request.source.path,
        node_function=function,
        enclosing_function=function,
        source_span=span,
        sink_span=span,
        normalized_formula=rendered,
        required_formula=rendered,
        raw_condition=parsed.text(node).strip(),
        structured_formula=expr_to_dict(formula),
        role="dataflow",
        path_id=stable_path_id("input", request.source.path, function, origin, span),
        origin=origin,
        control_origin="finite_def_use",
        promotable=False,
        confidence_reasons=["recognized fuzzer entry signature", "source expression mapping"],
        symbol_dependencies=symbols,
        semantic_tags=["dataflow", "fuzzer_input"],
    )


def analyze_fuzzer_input_mapping(
    parsed: ParsedSource, request: ExtractionRequest,
) -> list[ConstraintCandidate]:
    """Map recognized fuzzer buffer/size and explicit consumption operations."""
    candidates: list[ConstraintCandidate] = []
    for function in descendants(parsed.root, "function_definition"):
        if function_name(function, parsed) != "LLVMFuzzerTestOneInput":
            continue
        parameters = _parameter_names(function, parsed)
        if len(parameters) < 2:
            continue
        data, size = parameters[:2]
        declarator = function.child_by_field_name("declarator") or function
        mapping = and_expr((Compare("input_buffer", "==", data), Compare("input_size", "==", size)))
        candidates.append(_dataflow_candidate(
            parsed, declarator, request, mapping, "fuzzer_entry_mapping",
            f"Map fuzzer input to `{data}` buffer and `{size}` length", [data, size],
        ))
        for assignment in descendants(function, "assignment_expression"):
            left = assignment.child_by_field_name("left")
            right = assignment.child_by_field_name("right")
            operator_node = assignment.child_by_field_name("operator")
            if left is None or right is None or operator_node is None:
                continue
            left_text = parsed.text(left).strip()
            operator = parsed.text(operator_node).strip()
            right_text = parsed.text(right).strip()
            if left_text == data and operator == "+=":
                formula = Compare("input_offset_increment", "==", value_to_ir(right, parsed))
                candidates.append(_dataflow_candidate(
                    parsed, assignment, request, formula, "fuzzer_pointer_advance",
                    f"Advance fuzzer buffer `{data}` by `{right_text}`", [data, right_text],
                ))
            elif left_text == size and operator == "-=":
                formula = Compare("input_size_decrement", "==", value_to_ir(right, parsed))
                candidates.append(_dataflow_candidate(
                    parsed, assignment, request, formula, "fuzzer_length_decrement",
                    f"Reduce fuzzer length `{size}` by `{right_text}`", [size, right_text],
                ))
        for call in descendants(function, "call_expression"):
            leaf = callee_leaf(call, parsed)
            if not leaf.startswith("Consume"):
                continue
            formula = UnknownPredicate(parsed.text(call).strip(), "fuzzed_data_provider_consume")
            candidates.append(_dataflow_candidate(
                parsed, call, request, formula, "fuzzed_data_provider_consume",
                f"FuzzedDataProvider consumption `{parsed.text(call).strip()}` is source-backed but size semantics need review",
                [leaf],
            ))
    return candidates


__all__ = ["analyze_argument_bindings", "analyze_fuzzer_input_mapping", "fuzzer_input_symbols"]
