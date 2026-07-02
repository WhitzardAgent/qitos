"""Public orchestration API for bounded Level-1 C/C++ constraint analysis."""

from __future__ import annotations

import time
from dataclasses import replace
from itertools import islice
from typing import Any, Iterable

from .constraint_ast import (
    ParsedSource,
    call_matches,
    descendants,
    enclosing_function,
    function_matches,
    parse_source,
    target_span_matches,
    walk,
    _source_macro_models,
)
from .constraint_dataflow import analyze_argument_bindings, analyze_fuzzer_input_mapping
from .constraint_extractor import extract_path_constraints
from .constraint_models import (
    BudgetContext,
    BudgetExceeded,
    ConstraintCandidate,
    ConstraintPath,
    ExtractionDiagnostic,
    ExtractionRequest,
    ExtractionResult,
    ExtractionStats,
    stable_path_id,
)
from .constraint_sinks import analyze_sinks


def _path_formula(candidates: list[ConstraintCandidate]) -> str:
    parts: list[str] = []
    for candidate in candidates:
        formula = candidate.required_formula or candidate.normalized_formula
        if formula and formula not in parts:
            parts.append(f"({formula})" if " || " in formula else formula)
    return " && ".join(parts)


def _build_paths(
    candidates: list[ConstraintCandidate],
    request: ExtractionRequest,
    target_calls: list[Any],
    parsed: ParsedSource,
) -> tuple[list[ConstraintPath], list[str]]:
    grouped: dict[tuple[int, int], list[int]] = {}
    for index, candidate in enumerate(candidates):
        if candidate.role == "reachability" and candidate.target_call_span is not None:
            span = candidate.target_call_span
            grouped.setdefault((span.start_byte, span.end_byte), []).append(index)
    paths: list[ConstraintPath] = []
    truncated: list[str] = []
    seen: set[tuple[int, int]] = set()
    for call in target_calls:
        span = parsed.span(call)
        span_key = (span.start_byte, span.end_byte)
        if span_key in seen:
            continue
        seen.add(span_key)
        all_indexes = grouped.get(span_key, [])
        path_id = stable_path_id(
            "edge", request.source.path, request.caller_function, request.target_function, span,
        )
        if len(all_indexes) > request.budget.max_constraints_per_path:
            truncated.append(path_id)
        selected = tuple(all_indexes[:request.budget.max_constraints_per_path])
        for index in selected:
            candidates[index].path_id = path_id
            candidates[index].promotable = (
                candidates[index].role == "reachability" and candidates[index].confidence == "high"
            )
        paths.append(ConstraintPath(
            path_id=path_id,
            anchor_span=span,
            required_formula=_path_formula([candidates[index] for index in selected]) or "true",
            candidate_indexes=selected,
            target_function=request.target_function,
        ))
    return paths, truncated


def _target_calls(
    parsed: ParsedSource,
    request: ExtractionRequest,
    context: BudgetContext,
) -> tuple[list[Any], bool]:
    if not request.target_function:
        return [], False
    calls: list[Any] = []
    truncated = False
    for call in descendants(parsed.root, "call_expression"):
        context.checkpoint("target_callsites")
        if not call_matches(call, request.target_function, parsed):
            continue
        if not target_span_matches(call, parsed, request.target_callsite):
            continue
        _function_node, actual = enclosing_function(call, parsed)
        if request.caller_function and actual and not function_matches(actual, request.caller_function):
            continue
        if len(calls) >= request.budget.max_target_callsites:
            truncated = True
            break
        calls.append(call)
        context.callsites += 1
    return calls, truncated


def _unsupported_result(request: ExtractionRequest) -> ExtractionResult | None:
    source = request.source
    declared_language = (source.language or "").lower()
    suffix = source.file_extension.lower()
    if declared_language in {"rust", "swift"} or suffix in {".rs", ".swift"}:
        reason = "unsupported_language:C/C++ only"
        return ExtractionResult(
            diagnostics=[ExtractionDiagnostic(
                "unsupported_language", "Level-1 constraint analysis supports C and C++ only", "warning",
            )],
            unsupported_reasons=[reason],
        )
    return None


def _budget_diagnostic(result: ExtractionResult, exc: BudgetExceeded) -> None:
    result.stats.truncated = True
    result.diagnostics.append(ExtractionDiagnostic(
        "analysis_budget_exhausted", f"{exc.stage}: {exc.reason}", "warning",
    ))


def _derive_source_api_models(parsed: ParsedSource, request: ExtractionRequest):
    """Add only return semantics explicitly present in source definitions."""
    nullable = dict(request.api_models.nullable_returns)
    failures = dict(request.api_models.failure_returns)
    for function in descendants(parsed.root, "function_definition"):
        name_node = function.child_by_field_name("declarator")
        if name_node is None:
            continue
        from .constraint_ast import function_name
        name = function_name(function, parsed).rsplit("::", 1)[-1]
        returns = [parsed.text(item).strip() for item in descendants(function, "return_statement")]
        null_values: list[str] = []
        failure_values: list[str] = []
        for text in returns:
            value = text.removeprefix("return").removesuffix(";").strip()
            if value in {"NULL", "nullptr"}:
                null_values.append(value)
            elif value in {"-1", "EOF"}:
                failure_values.append(value)
        if null_values:
            nullable[name] = tuple(dict.fromkeys(null_values))
        if failure_values:
            failures[name] = tuple(dict.fromkeys(failure_values))
    return replace(request.api_models, nullable_returns=nullable, failure_returns=failures)


def _with_related_macro_models(
    parsed: ParsedSource, request: ExtractionRequest, context: BudgetContext,
) -> ParsedSource:
    transparent = set(parsed.transparent_boolean_macros)
    noreturn = set(parsed.noreturn_macros)
    defined = set(parsed.source_macros)
    for index, unit in enumerate(request.related_sources.values()):
        if index >= request.budget.max_related_sources:
            break
        context.checkpoint("related_macro_models")
        raw = unit.text if isinstance(unit.text, bytes) else unit.text.encode("utf-8", errors="replace")
        unit_transparent, unit_noreturn, unit_defined = _source_macro_models(raw)
        transparent.update(unit_transparent)
        noreturn.update(unit_noreturn)
        defined.update(unit_defined)
    return replace(
        parsed,
        transparent_boolean_macros=frozenset(transparent),
        noreturn_macros=frozenset(noreturn),
        source_macros=frozenset(defined),
    )


def _analyze_parsed(
    request: ExtractionRequest,
    parsed: ParsedSource,
    context: BudgetContext,
) -> ExtractionResult:
    source = request.source
    parsed = _with_related_macro_models(parsed, request, context)
    result = ExtractionResult(parse_language=parsed.language, parse_has_error=parsed.has_error)
    resolved_api_models = _derive_source_api_models(parsed, request)
    try:
        sample = list(islice(walk(parsed.root), request.budget.max_ast_nodes + 1))
        ast_truncated = len(sample) > request.budget.max_ast_nodes
        result.stats.ast_nodes_visited = min(len(sample), request.budget.max_ast_nodes)
        if ast_truncated:
            result.stats.truncated = True
            result.diagnostics.append(ExtractionDiagnostic(
                "ast_budget_exhausted",
                f"AST node budget {request.budget.max_ast_nodes} exhausted; analysis stopped before extraction",
                "warning",
            ))
            result.unsupported_reasons.append("ast_budget_exhausted")
            return result
        context.checkpoint("parsed_ast")

        edge_candidates: list[ConstraintCandidate] = []
        target_calls, callsites_truncated = _target_calls(parsed, request, context)
        if request.target_function:
            result.target_resolved = bool(target_calls)
            result.stats.target_callsites = len(target_calls)
            if callsites_truncated:
                result.stats.truncated = True
                result.diagnostics.append(ExtractionDiagnostic(
                    "callsite_budget_exhausted",
                    f"Target callsite budget {request.budget.max_target_callsites} exhausted",
                    "warning",
                ))
            if not target_calls:
                result.diagnostics.append(ExtractionDiagnostic(
                    "target_unresolved",
                    f"No exact callsite matched {request.caller_function or '?'} -> {request.target_function}",
                    "warning",
                ))
            else:
                noreturn = set(request.noreturn_functions) | set(resolved_api_models.noreturn_functions)
                edge_candidates = extract_path_constraints(
                    source.text,
                    source_path=source.path,
                    file_extension=source.file_extension,
                    caller_function=request.caller_function,
                    target_function=request.target_function,
                    target_callsite=request.target_callsite,
                    language=source.language,
                    noreturn_functions=noreturn,
                    source_line_offset=source.line_offset,
                    parsed_source=parsed,
                    max_target_callsites=request.budget.max_target_callsites,
                )
                allowed = {(parsed.span(call).start_byte, parsed.span(call).end_byte) for call in target_calls}
                edge_candidates = [
                    item for item in edge_candidates
                    if item.target_call_span
                    and (item.target_call_span.start_byte, item.target_call_span.end_byte) in allowed
                ]
                accepted_edge_candidates: list[ConstraintCandidate] = []
                for item in edge_candidates:
                    try:
                        context.consume_candidate("reachability")
                    except BudgetExceeded as exc:
                        _budget_diagnostic(result, exc)
                        break
                    item.role = "reachability"
                    item.required_formula = item.normalized_formula
                    item.confidence_reasons = list(dict.fromkeys([
                        *item.confidence_reasons, "exact target callsite", "source control dependence",
                    ]))
                    if source.completeness == "snippet":
                        item.confidence = "low"
                        item.confidence_score = min(item.confidence_score, 0.35)
                        item.promotable = False
                        item.confidence_reasons.append("incomplete source snippet")
                    accepted_edge_candidates.append(item)
                edge_candidates = accepted_edge_candidates

        result.candidates.extend(edge_candidates)
        result.paths, truncated_paths = _build_paths(result.candidates, request, target_calls, parsed)
        for path_id in truncated_paths:
            result.stats.truncated = True
            result.diagnostics.append(ExtractionDiagnostic(
                "path_constraint_budget_exhausted",
                f"Constraint budget exhausted for {path_id}",
                "warning",
            ))

        if context.exhausted_stages:
            return result

        context.checkpoint("dataflow")
        bindings, binding_diagnostics = analyze_argument_bindings(parsed, request, context)
        for item in bindings:
            context.consume_candidate("argument_binding")
            result.candidates.append(item)
        for item in analyze_fuzzer_input_mapping(parsed, request):
            context.consume_candidate("fuzzer_input_mapping")
            result.candidates.append(item)
        for code in binding_diagnostics:
            result.diagnostics.append(ExtractionDiagnostic(
                code, "Finite caller/callee binding analysis was incomplete", "warning",
            ))

        if request.sink_function or request.sink_span is not None:
            context.checkpoint("sink_analysis")
            sink = analyze_sinks(
                parsed,
                source_path=source.path,
                sink_function=request.sink_function,
                sink_span=request.sink_span,
                hint=request.vulnerability_hint,
                budget=request.budget,
                completeness=source.completeness,
                api_models=resolved_api_models,
                budget_context=context,
            )
            result.candidates.extend(sink.candidates)
            result.diagnostics.extend(sink.diagnostics)
            result.sink_resolved = sink.resolved
            result.stats.sink_anchors = sink.anchors
            result.stats.truncated = result.stats.truncated or sink.truncated
    except BudgetExceeded as exc:
        _budget_diagnostic(result, exc)
    finally:
        result.stats.candidates = len(result.candidates)
        result.stats.paths = len(result.paths)
        result.stats.elapsed_ms = (time.perf_counter() - context.started_at) * 1000
    return result


def analyze_constraints(request: ExtractionRequest) -> ExtractionResult:
    """Analyze one source unit without reading error, patch, or fixed-source evidence."""
    unsupported = _unsupported_result(request)
    if unsupported is not None:
        return unsupported
    context = BudgetContext(request.budget)
    parsed = parse_source(
        request.source.text,
        file_extension=request.source.file_extension,
        language=request.source.language,
        line_offset=request.source.line_offset,
        preferred_symbols=(
            request.caller_function, request.target_function, request.sink_function,
            *request.vulnerability_hint.symbols,
        ),
    )
    if parsed is None:
        return ExtractionResult(
            diagnostics=[ExtractionDiagnostic(
                "parser_unavailable", "Tree-sitter C/C++ parser could not parse this source unit", "error",
            )],
            unsupported_reasons=["parser_unavailable"],
        )
    return _analyze_parsed(request, parsed, context)


def analyze_constraint_requests(requests: Iterable[ExtractionRequest]) -> list[ExtractionResult]:
    """Analyze requests while parsing identical READ source units only once."""
    request_list = list(requests)
    results: list[ExtractionResult | None] = [None] * len(request_list)
    groups: dict[tuple[Any, ...], list[int]] = {}
    for index, request in enumerate(request_list):
        unsupported = _unsupported_result(request)
        if unsupported is not None:
            results[index] = unsupported
            continue
        source = request.source
        raw = source.text if isinstance(source.text, bytes) else source.text.encode("utf-8", errors="replace")
        key = (raw, source.file_extension, source.language, source.line_offset)
        groups.setdefault(key, []).append(index)
    for indexes in groups.values():
        first = request_list[indexes[0]]
        symbols: list[str] = []
        for index in indexes:
            request = request_list[index]
            symbols.extend((request.caller_function, request.target_function, request.sink_function))
            symbols.extend(request.vulnerability_hint.symbols)
        parsed = parse_source(
            first.source.text,
            file_extension=first.source.file_extension,
            language=first.source.language,
            line_offset=first.source.line_offset,
            preferred_symbols=tuple(dict.fromkeys(item for item in symbols if item)),
        )
        if parsed is None:
            for index in indexes:
                results[index] = ExtractionResult(
                    diagnostics=[ExtractionDiagnostic("parser_unavailable", "Tree-sitter parser unavailable", "error")],
                    unsupported_reasons=["parser_unavailable"],
                )
            continue
        for index in indexes:
            request = request_list[index]
            results[index] = _analyze_parsed(request, parsed, BudgetContext(request.budget))
    return [item for item in results if item is not None]


__all__ = ["analyze_constraint_requests", "analyze_constraints"]
