"""Tree-sitter repository indexing and summary construction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from ..agent_impl.constraint_ast import (
    callee_text, descendants, enclosing_function, function_name, parse_source, walk,
)
from ..agent_impl.constraint_extractor import extract_callsite_constraints
from .models import (
    CallCandidate, CallEdge, CallSite, ConstraintIR, DefinitionIR, ExprIR,
    FunctionSummary, FunctionSymbol, Parameter, RiskSignal, SourceLocation, stable_value,
)

_LOG = logging.getLogger(__name__)

SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"}
DEFAULT_EXCLUDES = {".git", "build", "dist", "out", "vendor", "third_party", "node_modules", "target"}

_CALL_RISK_KINDS = {
    "memcpy": ("memory_copy", .95), "memmove": ("memory_copy", .95),
    "strcpy": ("memory_copy", .95), "strncpy": ("memory_copy", .85),
    "sprintf": ("memory_copy", .85), "realloc": ("allocation", .80),
    "malloc": ("allocation", .65), "calloc": ("allocation", .65),
    "free": ("lifecycle", .75), "delete": ("lifecycle", .75),
    "read": ("io", .65), "write": ("io", .65),
    "fread": ("io", .70), "fwrite": ("io", .70),
}


def _identifier_dependencies(parsed: Any, node: Any, parameters: set[str]) -> list[str]:
    found = {
        parsed.text(item).strip() for item in walk(node)
        if item.type == "identifier" and parsed.text(item).strip() in parameters
    }
    return sorted(found)


def _risk_signal(parsed: Any, node: Any, file: str, function_id: str,
                 kind: str, severity: float, parameters: set[str], reason: str) -> RiskSignal:
    location = source_location(parsed, node, file)
    raw = parsed.text(node).strip()
    material = f"{function_id}|{kind}|{location.start_line}|{location.start_column}|{raw}"
    return RiskSignal(
        "risk_" + hashlib.blake2s(material.encode(), digest_size=7).hexdigest(),
        kind, raw[:300], location, severity,
        _identifier_dependencies(parsed, node, parameters), reason,
    )


def _extract_risk_signals(parsed: Any, fn: Any, file: str, function_id: str,
                          parameters: list[Parameter]) -> list[RiskSignal]:
    """Extract cheap navigation evidence while the file AST is already resident."""
    result: list[RiskSignal] = []
    seen: set[tuple[str, int, int]] = set()
    parameter_names = {item.name for item in parameters}
    for node in walk(fn):
        kind = ""
        severity = 0.0
        reason = ""
        raw = parsed.text(node).strip()
        if node.type == "subscript_expression":
            kind, severity, reason = "array_access", .85, "array index may require a bounds constraint"
        elif node.type in {"pointer_expression", "unary_expression"} and raw.startswith("*"):
            kind, severity, reason = "pointer_dereference", .75, "pointer dereference may require validity and lifetime constraints"
        elif "cast" in node.type:
            kind, severity, reason = "cast", .55, "cast may require a type, range, or alignment constraint"
        elif node.type == "call_expression":
            leaf = callee_text(node, parsed).rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1].lower()
            match = next(((name, value) for name, value in _CALL_RISK_KINDS.items() if leaf in {name, "__builtin_" + name}), None)
            if match:
                kind, severity = match[1]
                reason = f"call to {leaf} carries memory, I/O, or lifecycle semantics"
            elif any(token in leaf for token in ("copy", "insert", "append", "decode", "convert", "bytes2", "release", "destroy")):
                kind, severity, reason = "utility_call", .55, f"utility operation {leaf} may be the direct crash site"
        elif node.type == "binary_expression" and any(op in raw for op in (" + ", " - ", " * ", " / ", " << ", " >> ")):
            deps = _identifier_dependencies(parsed, node, parameter_names)
            if deps:
                kind, severity, reason = "input_arithmetic", .45, "arithmetic derived from a function parameter may affect a size or offset"
        elif node.type in {"for_statement", "while_statement", "do_statement"}:
            kind, severity, reason = "loop_progress", .35, "loop progress may depend on input-controlled state"
        if not kind:
            continue
        loc = source_location(parsed, node, file)
        key = (kind, loc.start_line, loc.start_column)
        if key in seen:
            continue
        seen.add(key)
        result.append(_risk_signal(parsed, node, file, function_id, kind, severity, parameter_names, reason))
    return sorted(result, key=lambda item: (-item.severity, item.location.start_line))[:64]


def source_location(parsed: Any, node: Any, file: str) -> SourceLocation:
    span = parsed.span(node)
    return SourceLocation(file, span.start_line, span.start_column, span.end_line, span.end_column)


def expr_ir(parsed: Any, node: Any, file: str) -> ExprIR:
    if node is None:
        return ExprIR("unknown", "missing")
    raw = parsed.text(node).strip()
    loc = source_location(parsed, node, file)
    t = node.type
    if t in {"identifier", "field_identifier", "qualified_identifier", "this"}:
        return ExprIR("identifier", raw, (), raw, loc)
    if t in {"number_literal", "char_literal", "string_literal", "true", "false"}:
        return ExprIR("constant", raw, (), raw, loc)
    if t in {"null", "nullptr"} or raw == "NULL":
        return ExprIR("null", "NULL", (), raw, loc)
    if t == "field_expression":
        base = node.child_by_field_name("argument") or node.child_by_field_name("value")
        field = node.child_by_field_name("field")
        op = "->" if "->" in raw else "."
        return ExprIR("pointer_field_access" if op == "->" else "field_access", parsed.text(field).strip() if field else "?", (expr_ir(parsed, base, file),), raw, loc)
    if t == "subscript_expression":
        base = node.child_by_field_name("argument") or (node.named_children[0] if node.named_children else None)
        idx = node.child_by_field_name("index") or (node.named_children[-1] if node.named_children else None)
        return ExprIR("array_access", None, (expr_ir(parsed, base, file), expr_ir(parsed, idx, file)), raw, loc)
    if t in {"binary_expression", "assignment_expression"}:
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        opn = node.child_by_field_name("operator")
        op = parsed.text(opn).strip() if opn else next((x for x in ("==", "!=", "<=", ">=", "&&", "||", "+", "-", "*", "/", "<", ">", "=") if x in raw), "?")
        return ExprIR("binary", op, (expr_ir(parsed, left, file), expr_ir(parsed, right, file)), raw, loc)
    if t in {"unary_expression", "pointer_expression"}:
        arg = node.child_by_field_name("argument") or (node.named_children[-1] if node.named_children else None)
        opn = node.child_by_field_name("operator")
        op = parsed.text(opn).strip() if opn else raw[:1]
        kind = "address_of" if op == "&" else "dereference" if op == "*" else "unary"
        return ExprIR(kind, op, (expr_ir(parsed, arg, file),), raw, loc)
    if t == "call_expression":
        args = node.child_by_field_name("arguments")
        return ExprIR("call", callee_text(node, parsed), tuple(expr_ir(parsed, x, file) for x in (args.named_children if args else ())), raw, loc)
    if "cast" in t:
        child = node.child_by_field_name("value") or (node.named_children[-1] if node.named_children else None)
        return ExprIR("cast", raw.split("(", 1)[0], (expr_ir(parsed, child, file),), raw, loc)
    if t == "conditional_expression":
        return ExprIR("conditional", None, tuple(expr_ir(parsed, x, file) for x in node.named_children), raw, loc)
    if t in {"initializer_list", "argument_list"}:
        return ExprIR("conditional", "alternatives", tuple(expr_ir(parsed, x, file) for x in node.named_children), raw, loc)
    return ExprIR("unknown", raw, (), raw, loc)


def legacy_expr_ir(value: dict[str, Any], location: SourceLocation) -> ExprIR:
    kind = str(value.get("kind") or "unknown")
    raw = str(value.get("raw") or "")
    if kind == "IdentifierValue": return ExprIR("identifier", value.get("name"), (), raw, location)
    if kind == "LiteralValue": return ExprIR("constant", value.get("value"), (), raw, location)
    if kind in {"RawValue", "UnknownPredicate"}: return ExprIR("unknown", raw, (), raw, location)
    if kind in {"Compare", "BinaryValue", "BitmaskPredicate"}:
        left = value.get("left") or value.get("value") or {}
        right = value.get("right") or value.get("expected") or {}
        return ExprIR("binary", value.get("operator"), (legacy_expr_ir(left if isinstance(left, dict) else {"kind":"RawValue","raw":left}, location), legacy_expr_ir(right if isinstance(right, dict) else {"kind":"RawValue","raw":right}, location)), raw, location)
    if kind in {"And", "Or"}:
        op = "&&" if kind == "And" else "||"; terms = [legacy_expr_ir(x, location) for x in value.get("terms", [])]
        if not terms: return ExprIR("unknown", raw, (), raw, location)
        result = terms[0]
        for term in terms[1:]: result = ExprIR("binary", op, (result, term), raw, location)
        return result
    if kind == "Not": return ExprIR("unary", "!", (legacy_expr_ir(value.get("operand", {}), location),), raw, location)
    return ExprIR("unknown", raw or value, (), raw, location)


def _declarator_name(parsed: Any, node: Any) -> str:
    ids = [x for x in walk(node) if x.type in {"identifier", "field_identifier"}]
    return parsed.text(ids[-1]).strip() if ids else ""


def _parameters(parsed: Any, fn: Any) -> list[Parameter]:
    declarator = fn.child_by_field_name("declarator")
    plist = next((x for x in walk(declarator) if x.type == "parameter_list"), None) if declarator else None
    result: list[Parameter] = []
    for index, node in enumerate(plist.named_children if plist else ()):
        if node.type not in {"parameter_declaration", "optional_parameter_declaration"}:
            continue
        name = _declarator_name(parsed, node)
        raw = parsed.text(node).strip()
        if not name:
            name = f"$arg{index}"
        result.append(Parameter(name, raw[: max(0, raw.rfind(name))].strip() if name in raw else raw))
    return result


def _qualified_name(parsed: Any, fn: Any) -> str:
    name = function_name(fn, parsed)
    scopes: list[str] = []
    parent = fn.parent
    while parent is not None:
        if parent.type in {"namespace_definition", "class_specifier", "struct_specifier"}:
            name_node = parent.child_by_field_name("name")
            if name_node is not None:
                scopes.append(parsed.text(name_node).strip())
        parent = parent.parent
    return "::".join([*reversed(scopes), name]) if scopes else name


def _symbol_id(rel: str, qualified: str, parameters: list[Parameter], is_static: bool) -> str:
    static = "static::" if is_static else ""
    signature = ",".join(item.type_text for item in parameters)
    signature_id = hashlib.blake2s(signature.encode("utf-8", errors="replace"), digest_size=4).hexdigest()
    return f"{rel}::{static}{qualified}/{len(parameters)}@{signature_id}"


def scan_files(root: Path, *, excludes: set[str] | None = None, max_files: int = 50_000, max_file_size: int = 5 * 1024 * 1024) -> list[Path]:
    ignored = DEFAULT_EXCLUDES | set(excludes or ())
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ignored)
        for name in sorted(names):
            path = Path(current) / name
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            try:
                if path.stat().st_size <= max_file_size:
                    files.append(path)
            except OSError:
                continue
            if len(files) >= max_files:
                return files
    return files


def index_file(root: Path, path: Path) -> tuple[str, list[FunctionSymbol], list[FunctionSummary], list[dict[str, Any]]]:
    rel = path.relative_to(root).as_posix()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        parsed = parse_source(raw, file_extension=path.suffix)
    except Exception:
        return digest, [], [], [{"file": rel, "reason": "parse_exception"}]
    if parsed is None:
        return digest, [], [], [{"file": rel, "reason": "tree_sitter_unavailable"}]
    symbols: list[FunctionSymbol] = []
    summaries: list[FunctionSummary] = []
    unresolved: list[dict[str, Any]] = []
    fn_index = 0
    for fn in descendants(parsed.root, "function_definition"):
        fn_index += 1
        try:
            qualified = _qualified_name(parsed, fn)
            name = qualified.rsplit("::", 1)[-1]
            params = _parameters(parsed, fn)
            prefix = parsed.text(fn)[: max(0, parsed.text(fn).find("{"))]
            is_static = "static" in prefix.split()
            sid = _symbol_id(rel, qualified, params, is_static)
            symbol = FunctionSymbol(sid, name, qualified, rel, qualified.rsplit("::", 1)[0] if "::" in qualified else None, params, is_static, parsed.language, source_location(parsed, fn, rel), parsed.text(fn)[:500])
            summary = FunctionSummary(sid, [p.name for p in params])
            summary.risk_signals = _extract_risk_signals(parsed, fn, rel, sid, params)
            detailed_constraints = symbol.body_location.end_line - symbol.body_location.start_line <= 5000
            local_types: dict[str, str] = {}
            for declaration in descendants(fn, "declaration"):
                type_node = declaration.child_by_field_name("type")
                type_text = parsed.text(type_node).strip() if type_node is not None else ""
                for declarator in declaration.named_children:
                    if declarator is type_node:
                        continue
                    variable = _declarator_name(parsed, declarator)
                    if variable and type_text:
                        local_types[variable] = type_text
            for call in descendants(fn, "call_expression"):
                args = call.child_by_field_name("arguments")
                call_loc = source_location(parsed, call, rel)
                cid = f"{sid}@{call_loc.start_line}:{call_loc.start_column}"
                full = callee_text(call, parsed)
                receiver_node = call.child_by_field_name("function")
                receiver = None
                if receiver_node is not None and receiver_node.type == "field_expression":
                    receiver = expr_ir(parsed, receiver_node.child_by_field_name("argument"), rel)
                receiver_type = local_types.get(str(receiver.value), "") if receiver and receiver.kind == "identifier" else ""
                guards: list[ConstraintIR] = []
                constraint_items = extract_callsite_constraints(call, parsed, source_path=rel, caller_function=qualified, target_function=full) if detailed_constraints else []
                for item in constraint_items:
                    loc = item.source_span
                    if loc is None:
                        continue
                    evidence_loc = SourceLocation(rel, loc.start_line, loc.start_column, loc.end_line, loc.end_column)
                    e = legacy_expr_ir(item.structured_formula, evidence_loc)
                    guards.append(ConstraintIR(
                        e, item.raw_condition, e.render(), True, sid, evidence_loc,
                        item.origin, item.confidence_score,
                        role=getattr(item, "role", "reachability"),
                        gate_type=getattr(item, "gate_type", "path_gate"),
                        safe_formula=getattr(item, "safe_formula", ""),
                        violation_formula=getattr(item, "violation_formula", ""),
                    ))
                summary.calls.append(CallSite(cid, sid, full, receiver, [expr_ir(parsed, x, rel) for x in (args.named_children if args else ())], call_loc, guards, receiver_type=receiver_type))
            if not detailed_constraints:
                summary.unresolved_nodes.append({"reason": "max_function_lines_exceeded", "location": stable_location(symbol.body_location)})
            for ret in descendants(fn, "return_statement"):
                if ret.named_children:
                    summary.returns.append(expr_ir(parsed, ret.named_children[-1], rel))
                summary.early_exits.append(source_location(parsed, ret, rel))
            for node in walk(fn):
                if node.type in {"init_declarator", "assignment_expression"}:
                    left = node.child_by_field_name("declarator") or node.child_by_field_name("left")
                    right = node.child_by_field_name("value") or node.child_by_field_name("right")
                    name_text = (_declarator_name(parsed, left) if node.type == "init_declarator" else parsed.text(left).strip()) if left is not None else ""
                    if name_text and right is not None:
                        definition = DefinitionIR(name_text, expr_ir(parsed, right, rel), source_location(parsed, node, rel))
                        (summary.field_writes if "." in name_text or "->" in name_text else summary.local_definitions).append(definition)
                elif node.type == "ERROR" or node.is_missing:
                    summary.unresolved_nodes.append({"reason": "parse_error", "location": source_location(parsed, node, rel).__dict__})
            symbols.append(symbol); summaries.append(summary)
        except Exception as exc:
            _LOG.warning("index_file: error processing function #%d in %s: %s", fn_index, rel, exc)
            unresolved.append({"file": rel, "reason": "function_index_error", "function_index": fn_index, "detail": str(exc)[:200]})
            continue
    if parsed.has_error:
        unresolved.append({"file": rel, "reason": "partial_parse", "error_count": parsed.error_count})
    return digest, symbols, summaries, unresolved


def stable_location(location: SourceLocation) -> dict[str, Any]:
    return {"file": location.file, "start_line": location.start_line, "start_column": location.start_column, "end_line": location.end_line, "end_column": location.end_column}


def resolve_calls(symbols: list[FunctionSymbol], summaries: list[FunctionSummary]) -> list[CallEdge]:
    by_name: dict[str, list[FunctionSymbol]] = {}
    by_id = {s.symbol_id: s for s in symbols}
    for symbol in symbols:
        by_name.setdefault(symbol.name, []).append(symbol)
        by_name.setdefault(symbol.qualified_name, []).append(symbol)
    edges: list[CallEdge] = []
    for summary in summaries:
        caller = by_id.get(summary.function_id)
        for call in summary.calls:
            leaf = call.callee_text.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]
            pointer_name = leaf.split("[", 1)[0]
            candidates = list(dict.fromkeys(s.symbol_id for s in by_name.get(call.callee_text, []) + by_name.get(leaf, [])))
            if call.receiver_type:
                candidates.extend(s.symbol_id for s in by_name.get(f"{call.receiver_type}::{leaf}", []))
                candidates = list(dict.fromkeys(candidates))
            pointer_defs = [d for d in summary.local_definitions + summary.field_writes if d.target in {leaf, pointer_name} or d.target.endswith("." + pointer_name)]
            pointer_targets: list[str] = []
            def collect_targets(expression: ExprIR) -> None:
                if expression.kind == "identifier": pointer_targets.append(str(expression.value))
                for child in expression.children: collect_targets(child)
            for definition in pointer_defs: collect_targets(definition.expression)
            for target_name in pointer_targets:
                candidates.extend(s.symbol_id for s in by_name.get(target_name, []))
            candidates = list(dict.fromkeys(candidates))
            ranked: list[CallCandidate] = []
            for sid in candidates:
                target = by_id[sid]
                if len(target.parameters) != len(call.arguments):
                    continue
                if target.is_static and (caller is None or target.file != caller.file):
                    continue
                if target.name in pointer_targets:
                    kind, confidence = ("function_pointer_table", .60) if "[" in call.callee_text else ("function_pointer_assignment", .60)
                elif target.is_static and caller and target.file == caller.file:
                    kind, confidence = "same_file_static", 1.0
                elif "::" in call.callee_text and target.qualified_name.endswith(call.callee_text):
                    kind, confidence = "qualified_name", .95
                elif call.receiver_type and target.qualified_name.endswith(f"{call.receiver_type}::{leaf}"):
                    kind, confidence = "explicit_receiver_type", .70
                elif caller and target.file == caller.file:
                    kind, confidence = "same_file", .90
                elif len(candidates) == 1:
                    kind, confidence = "cross_file_unique", .85
                else:
                    kind, confidence = "name_arity_candidate", .45
                ranked.append(CallCandidate(sid, kind, confidence, [f"definition {target.file}:{target.body_location.start_line}"]))
            call.candidates = sorted(ranked, key=lambda x: -x.confidence)[:10]
            call.resolution_status = "resolved_unique" if len(call.candidates) == 1 else "resolved_candidates" if call.candidates else "unresolved"
            for candidate in call.candidates:
                target = by_id[candidate.symbol_id]
                bindings = {p.name: a for p, a in zip(target.parameters, call.arguments)}
                edges.append(CallEdge(summary.function_id, target.symbol_id, call.callsite_id, bindings, call.local_guards, candidate.resolution_kind, candidate.confidence, candidate.evidence))
    return edges


# ---------------------------------------------------------------------------
# Subprocess isolation for SIGSEGV resilience
# ---------------------------------------------------------------------------

def index_file_isolated(root: Path, path: Path, *, timeout: float = 30.0) -> tuple[str, list[FunctionSymbol], list[FunctionSummary], list[dict[str, Any]]]:
    """Index a single file in an isolated subprocess.

    If the subprocess crashes (SIGSEGV, SIGABRT, timeout), return partial
    results so the rest of the repository can still be indexed.
    """
    rel = path.relative_to(root).as_posix()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parents[2])
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (package_parent, existing_pythonpath) if item
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", f"{__package__}._index_worker"],
            input=json.dumps({"root": str(root), "path": str(path)}).encode(),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        _LOG.warning("index_file subprocess timed out for %s (%.0fs)", rel, timeout)
        return digest, [], [], [{"file": rel, "reason": "index_timeout"}]
    except Exception as exc:
        _LOG.warning("index_file subprocess failed for %s: %s", rel, exc)
        return digest, [], [], [{"file": rel, "reason": "subprocess_error", "detail": str(exc)[:200]}]

    if result.returncode != 0:
        reason = "subprocess_error"
        if result.returncode == -11 or result.returncode == 139:
            reason = "sigsegv_crash"
        elif result.returncode == -6 or result.returncode == 134:
            reason = "sigabrt_crash"
        elif result.returncode == 1:
            # Python exception — stderr may have details
            stderr = result.stderr.decode("utf-8", errors="replace")[-300:]
            _LOG.warning("index_file subprocess error for %s: %s", rel, stderr)
            reason = "index_exception"
        _LOG.warning("index_file subprocess exit=%d for %s (reason=%s)", result.returncode, rel, reason)
        return digest, [], [], [{"file": rel, "reason": reason, "exit_code": result.returncode}]

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _LOG.warning("index_file subprocess bad output for %s: %s", rel, exc)
        return digest, [], [], [{"file": rel, "reason": "bad_output", "detail": str(exc)[:200]}]

    symbols = [_symbol_from_dict(v) for v in data.get("symbols", [])]
    summaries = [_summary_from_dict(v) for v in data.get("summaries", [])]
    unresolved = data.get("unresolved", [])
    return digest, symbols, summaries, unresolved


def _symbol_from_dict(v: dict[str, Any]) -> FunctionSymbol:
    return FunctionSymbol(
        v["symbol_id"], v["name"], v["qualified_name"], v["file"],
        v.get("scope"), [Parameter(**p) for p in v.get("parameters", [])],
        bool(v.get("is_static")), v.get("language", "c"),
        SourceLocation(**v["body_location"]) if v.get("body_location") else SourceLocation("", 0),
        v.get("source_text", ""),
    )


def _summary_from_dict(v: dict[str, Any]) -> FunctionSummary:
    return FunctionSummary(
        v["function_id"],
        list(v.get("parameters", [])),
        [_call_from_dict(c) for c in v.get("calls", [])],
        [_expr_from_dict(x) for x in v.get("returns", [])],
        [_definition_from_dict(d) for d in v.get("local_definitions", [])],
        [_definition_from_dict(d) for d in v.get("field_writes", [])],
        [SourceLocation(**e) if isinstance(e, dict) else e for e in v.get("early_exits", [])],
        list(v.get("unresolved_nodes", [])),
        [RiskSignal(
            x["signal_id"], x["kind"], x.get("expression", ""),
            SourceLocation(**x["location"]), float(x.get("severity", 0)),
            list(x.get("parameter_dependencies", [])), x.get("reason", ""),
        ) for x in v.get("risk_signals", [])],
    )


def _call_from_dict(v: dict[str, Any]) -> CallSite:
    return CallSite(
        v["callsite_id"], v["caller_id"], v["callee_text"],
        _expr_from_dict(v["receiver"]) if v.get("receiver") else None,
        [_expr_from_dict(x) for x in v.get("arguments", [])],
        SourceLocation(**v["location"]) if v.get("location") else SourceLocation("", 0),
        [_constraint_from_dict(g) for g in v.get("local_guards", [])],
        [CallCandidate(**c) for c in v.get("candidates", [])],
        v.get("resolution_status", "unresolved"),
        v.get("receiver_type", ""),
    )


def _constraint_from_dict(v: dict[str, Any]) -> ConstraintIR:
    return ConstraintIR(
        _expr_from_dict(v["expression"]), v["source_text"], v["normalized_text"],
        bool(v["polarity"]), v["origin_function"],
        SourceLocation(**v["origin_location"]) if v.get("origin_location") else SourceLocation("", 0),
        v["reason"], float(v.get("confidence", 0)),
        v.get("role", "reachability"), v.get("gate_type", "path_gate"),
        v.get("safe_formula", ""), v.get("violation_formula", ""),
        v.get("input_mapping", ""),
    )


def _definition_from_dict(v: dict[str, Any]) -> DefinitionIR:
    return DefinitionIR(
        v["target"], _expr_from_dict(v["expression"]),
        SourceLocation(**v["location"]) if v.get("location") else SourceLocation("", 0),
    )


def _expr_from_dict(v: dict[str, Any]) -> ExprIR:
    if v is None:
        return ExprIR("unknown", "missing")
    return ExprIR(
        v.get("kind", "unknown"), v.get("value"),
        tuple(_expr_from_dict(c) for c in v.get("children", [])),
        v.get("source_text", ""),
        SourceLocation(**v["location"]) if v.get("location") else None,
    )
