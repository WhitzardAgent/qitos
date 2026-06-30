"""AST-level path constraint extraction using tree-sitter.

Replaces the regex-based Patterns 2-4 in agent.py with proper AST
analysis that can:
- Distinguish early-exit guards (AVOID) from path requirements (SATISFY)
- Handle multi-line conditions, braced bodies, compound booleans
- Filter loop-counter variables from bounds suggestions
- Extract switch-case routing with target-function awareness
- Detect memcmp/strcmp format checks reliably

Pattern 1 (format_gate from memcmp/strcmp) is also migrated here
for unified entry.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Lazy imports — tree-sitter may not be installed in all environments.
_TS_AVAILABLE: Optional[bool] = None
_C_LANG = None
_CPP_LANG = None
_PARSER = None


def _ensure_tree_sitter() -> bool:
    global _TS_AVAILABLE, _C_LANG, _CPP_LANG, _PARSER
    if _TS_AVAILABLE is not None:
        return _TS_AVAILABLE
    try:
        import tree_sitter_c as tsc
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser

        _C_LANG = Language(tsc.language())
        _CPP_LANG = Language(tscpp.language())
        _PARSER = Parser()
        _TS_AVAILABLE = True
    except ImportError:
        _TS_AVAILABLE = False
    return _TS_AVAILABLE


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ConstraintCandidate:
    """One extracted constraint awaiting LLM judgment or direct promotion."""

    gate_type: str          # format_gate | path_gate | dispatch_gate | bounds_gate | value_gate
    description: str        # Human-readable description
    required_condition: str # Positive condition for PoC construction
    polarity: str           # "satisfy" | "avoid"
    confidence: str         # "high" | "medium" | "low"
    source: str             # File path where this was found
    node_function: str = "" # Chain node function this gate belongs to


# ---------------------------------------------------------------------------
# Inversion helpers
# ---------------------------------------------------------------------------

_INVERT_OP = {
    "<": ">=", "<=": ">", ">": "<=", ">=": "<",
    "==": "!=", "!=": "==",
    "&&": "||", "||": "&&",  # logical inversion (for display)
}

_TRIVIAL_VARS = frozenset({
    "i", "j", "k", "idx", "index", "n", "count", "len",
    "ret", "rc", "r", "err", "res", "status",
})


def _invert_comparison(op: str) -> str:
    return _INVERT_OP.get(op, op)


# ---------------------------------------------------------------------------
# AST walking helpers
# ---------------------------------------------------------------------------

def _find_all(node: Any, type_name: str) -> List[Any]:
    """Depth-first collect all descendants of a given type."""
    results: List[Any] = []
    if node.type == type_name:
        results.append(node)
    for child in node.children:
        results.extend(_find_all(child, type_name))
    return results


def _contains_early_exit(node: Any) -> bool:
    """Check if a node's direct children contain return/goto/break/continue."""
    EXIT_TYPES = {"return_statement", "goto_statement", "break_statement", "continue_statement"}
    for child in node.children:
        if child.type in EXIT_TYPES:
            return True
        # Check inside compound_statement (braced body)
        if child.type == "compound_statement":
            for stmt in child.children:
                if stmt.type in EXIT_TYPES:
                    return True
    return False


def _extract_condition_text(if_node: Any, source: bytes) -> str:
    """Extract the condition text from an if_statement node."""
    for child in if_node.children:
        if child.type == "parenthesized_expression":
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace").strip()
    return ""


def _extract_comparison_from_condition(cond_text: str) -> Optional[Dict[str, str]]:
    """Parse a simple comparison like 'size < 16' or 'tag == 0xFFD8'."""
    m = _re.match(
        r'\(\s*(\w+)(?:\s*([+\-])\s*(\w+))?\s*([<>=!]+)\s*(0x[\da-fA-F]+|\d+)\s*\)',
        cond_text,
    )
    if not m:
        return None
    var, op, offset_var, cmp_op, threshold = (
        m.group(1), m.group(2) or "", m.group(3) or "",
        m.group(4), m.group(5),
    )
    expr = f"{var}{op}{offset_var}" if offset_var else var
    return {"expr": expr, "cmp_op": cmp_op, "threshold": threshold, "var": var}


def _extract_string_literal(node: Any, source: bytes) -> str:
    """Extract a string literal from a call_expression argument."""
    for child in node.children:
        if child.type == "string_literal":
            text = source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            # Strip quotes
            if len(text) >= 2 and text[0] in ('"', "'"):
                return text[1:-1]
    return ""


def _find_call_name(call_node: Any, source: bytes) -> str:
    """Extract the function name from a call_expression."""
    for child in call_node.children:
        if child.type == "identifier":
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        if child.type == "field_identifier":
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        if child.type in ("scoped_identifier", "scoped_field_identifier"):
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


# ---------------------------------------------------------------------------
# Loop variable extraction
# ---------------------------------------------------------------------------

def _extract_loop_variables(root: Any, source: bytes) -> Set[str]:
    """Collect variable names declared in for-statement initializers."""
    loop_vars: Set[str] = set()
    for for_node in _find_all(root, "for_statement"):
        for child in for_node.children:
            if child.type in ("declaration", "init_declarator"):
                for sub in child.children:
                    if sub.type == "identifier":
                        name = source[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                        loop_vars.add(name)
            # C99-style: for (int i = 0; ...)
            if child.type == "declaration":
                ids = _find_all(child, "identifier")
                for ident in ids:
                    name = source[ident.start_byte:ident.end_byte].decode("utf-8", errors="replace")
                    # Only single-char or common loop var names
                    if len(name) <= 2 or name in _TRIVIAL_VARS:
                        loop_vars.add(name)
    return loop_vars


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_path_constraints(
    source_text: str,
    source_path: str = "",
    known_functions: Optional[Set[str]] = None,
    file_extension: str = ".c",
) -> List[ConstraintCandidate]:
    """Extract path constraints from source code using tree-sitter AST.

    Falls back to regex Pattern 1 (memcmp/strcmp format_gate) if
    tree-sitter is unavailable.
    """
    candidates: List[ConstraintCandidate] = []

    if not _ensure_tree_sitter():
        # Fallback: regex Pattern 1 only
        return _extract_format_gates_regex(source_text, source_path)

    source = source_text.encode("utf-8")
    lang = _CPP_LANG if file_extension in (".cpp", ".cc", ".cxx", ".hpp", ".h") else _C_LANG

    try:
        _PARSER.language = lang
        tree = _PARSER.parse(source)
    except Exception:
        return _extract_format_gates_regex(source_text, source_path)

    root = tree.root_node
    loop_vars = _extract_loop_variables(root, source)
    known = known_functions or set()

    # --- if-statement analysis ---
    for if_node in _find_all(root, "if_statement"):
        cond_text = _extract_condition_text(if_node, source)
        if not cond_text:
            continue

        is_early_exit = _contains_early_exit(if_node)

        # Check for memcmp/strcmp format gates
        format_gate = _check_format_gate(if_node, source, source_path, cond_text)
        if format_gate:
            candidates.append(format_gate)
            continue

        # Simple comparison extraction
        comp = _extract_comparison_from_condition(cond_text)
        if comp and comp["var"].lower() not in _TRIVIAL_VARS and comp["var"] not in loop_vars:
            if is_early_exit:
                inv_op = _invert_comparison(comp["cmp_op"])
                candidates.append(ConstraintCandidate(
                    gate_type="bounds_gate",
                    description=f"Early-exit guard: {comp['expr']} {inv_op} {comp['threshold']} at {source_path}",
                    required_condition=f"{comp['expr']} must be {inv_op} {comp['threshold']} (to pass this guard)",
                    polarity="avoid",
                    confidence="medium",
                    source=source_path,
                ))
            else:
                candidates.append(ConstraintCandidate(
                    gate_type="bounds_gate",
                    description=f"Bounds check: {comp['expr']} {comp['cmp_op']} {comp['threshold']} at {source_path}",
                    required_condition=f"{comp['expr']} must be {comp['cmp_op']} {comp['threshold']}",
                    polarity="satisfy",
                    confidence="medium",
                    source=source_path,
                ))
            continue

        # Guard condition (simple variable check, like `if (!ptr) return`)
        guard = _check_simple_guard(if_node, source, source_path, cond_text, is_early_exit)
        if guard:
            candidates.append(guard)
            continue

        # Compound boolean with early exit: `if (a && b) return`
        if is_early_exit:
            compound = _check_compound_guard(cond_text, source_path)
            if compound:
                candidates.extend(compound)

    # --- switch-case routing ---
    for switch_node in _find_all(root, "switch_statement"):
        dispatch = _check_switch_dispatch(switch_node, source, source_path, known)
        if dispatch:
            candidates.append(dispatch)

    return candidates


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_format_gate(
    if_node: Any, source: bytes, source_path: str, cond_text: str,
) -> Optional[ConstraintCandidate]:
    """Check if an if-statement contains a memcmp/strcmp format check."""
    FORMAT_FUNCS = {"memcmp", "strcmp", "strncmp", "strncasecmp", "strcasecmp"}
    for call_node in _find_all(if_node, "call_expression"):
        func_name = _find_call_name(call_node, source)
        if func_name in FORMAT_FUNCS:
            # Find string literal argument
            magic = ""
            for arg in call_node.children:
                if arg.type == "argument_list":
                    for a in arg.children:
                        if a.type == "string_literal":
                            raw = source[a.start_byte:a.end_byte].decode("utf-8", errors="replace")
                            if len(raw) >= 2 and raw[0] == '"':
                                magic = raw[1:-1]
                                break
            if magic:
                return ConstraintCandidate(
                    gate_type="format_gate",
                    description=f"Must match '{magic}' (comparison at {source_path})",
                    required_condition=f"Input must contain '{magic}' at the comparison offset",
                    polarity="satisfy",
                    confidence="high",
                    source=source_path,
                )
    return None


def _check_simple_guard(
    if_node: Any, source: bytes, source_path: str,
    cond_text: str, is_early_exit: bool,
) -> Optional[ConstraintCandidate]:
    """Check for simple guard like `if (!ptr) return` or `if (ptr) return`."""
    # Pattern: if (!var) return  →  var must be true/non-zero
    # Pattern: if (var) return   →  var must be false/zero
    m = _re.match(r'\(\s*!\s*(\w+)\s*\)', cond_text)
    if m:
        var = m.group(1)
        if var.lower() in _TRIVIAL_VARS:
            return None
        if is_early_exit:
            return ConstraintCandidate(
                gate_type="path_gate",
                description=f"Early-exit guard: {var} must be true to avoid early return at {source_path}",
                required_condition=f"Avoid early exit: {var} must be true/non-zero (else returns early)",
                polarity="avoid",
                confidence="medium",
                source=source_path,
            )
    # Pattern: if (var) return  →  var must be false/zero to continue
    m2 = _re.match(r'\(\s*(\w+)\s*\)', cond_text)
    if m2:
        var = m2.group(1)
        if var.lower() in _TRIVIAL_VARS or var in ("NULL", "nullptr", "0", "null"):
            return None
        if is_early_exit:
            return ConstraintCandidate(
                gate_type="path_gate",
                description=f"Early-exit guard: {var} must be false/zero to avoid early return at {source_path}",
                required_condition=f"Avoid early exit: {var} must be false/zero (else returns early)",
                polarity="avoid",
                confidence="medium",
                source=source_path,
            )
    return None


def _check_compound_guard(
    cond_text: str, source_path: str,
) -> List[ConstraintCandidate]:
    """Decompose compound boolean with early exit: `if (a && b) return`."""
    results: List[ConstraintCandidate] = []
    # Split on && or ||
    parts = _re.split(r'\s*(?:&&|\|\|)\s*', cond_text.strip("()"))
    if len(parts) <= 1:
        return results

    for part in parts[:4]:  # Cap at 4 sub-conditions
        part = part.strip()
        if not part:
            continue
        # Try to extract a comparison
        comp = _extract_comparison_from_condition(f"({part})")
        if comp and comp["var"].lower() not in _TRIVIAL_VARS:
            inv_op = _invert_comparison(comp["cmp_op"])
            results.append(ConstraintCandidate(
                gate_type="bounds_gate",
                description=f"Compound guard: {comp['expr']} {inv_op} {comp['threshold']} at {source_path}",
                required_condition=f"{comp['expr']} must be {inv_op} {comp['threshold']} (part of compound guard)",
                polarity="avoid",
                confidence="low",
                source=source_path,
            ))
    return results


def _check_switch_dispatch(
    switch_node: Any, source: bytes, source_path: str,
    known_functions: Set[str],
) -> Optional[ConstraintCandidate]:
    """Extract dispatch routing from a switch statement."""
    # Find the switch variable
    switch_var = ""
    for child in switch_node.children:
        if child.type == "parenthesized_expression":
            for sub in child.children:
                if sub.type == "identifier":
                    switch_var = source[sub.start_byte:sub.end_byte].decode("utf-8", errors="replace")
                    break
    if not switch_var:
        return None

    # Collect case values
    cases: List[str] = []
    target_cases: List[str] = []
    for case_node in _find_all(switch_node, "case_statement"):
        for child in case_node.children:
            if child.type == "number_literal":
                val = source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                cases.append(val)
                # Check if this case body calls a known target function
                if known_functions:
                    case_text = source[case_node.start_byte:case_node.end_byte].decode("utf-8", errors="replace")
                    for func in known_functions:
                        if func in case_text:
                            target_cases.append(val)
                break

    if not cases:
        return None

    case_str = ", ".join(cases[:6])
    desc = f"Dispatch on {switch_var}: cases {case_str} at {source_path}"
    cond = f"{switch_var} must equal one of [{case_str}] to reach the target handler"

    confidence = "medium"
    if target_cases:
        target_str = ", ".join(target_cases[:3])
        desc += f" (target at case {target_str})"
        cond = f"{switch_var} must equal {target_str} to reach the target function"
        confidence = "high"

    return ConstraintCandidate(
        gate_type="dispatch_gate",
        description=desc,
        required_condition=cond,
        polarity="satisfy",
        confidence=confidence,
        source=source_path,
    )


# ---------------------------------------------------------------------------
# Regex fallback (Pattern 1 only)
# ---------------------------------------------------------------------------

def _extract_format_gates_regex(
    source_text: str, source_path: str,
) -> List[ConstraintCandidate]:
    """Fallback regex extraction for memcmp/strcmp format checks."""
    candidates: List[ConstraintCandidate] = []
    for m in _re.finditer(
        r'(?:if|assert)\s*\([^)]*(?:memcmp|strcmp|strncmp|strncasecmp)\s*\(\s*[^,]+,\s*"([^"]+)"',
        source_text,
    ):
        magic = m.group(1)
        candidates.append(ConstraintCandidate(
            gate_type="format_gate",
            description=f"Must match '{magic}' (comparison at {source_path})",
            required_condition=f"Input must contain '{magic}' at the comparison offset",
            polarity="satisfy",
            confidence="high",
            source=source_path,
        ))
    return candidates
