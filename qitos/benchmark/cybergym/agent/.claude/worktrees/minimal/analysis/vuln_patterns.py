"""C/C++ unsafe-function vulnerability pattern catalog.

This module provides:
1. A catalog of known-unsafe C/C++ API functions with risk levels and safe
   alternatives — used to tag risk signals during indexing.
2. Stdlib/builtin classification — identifying which function calls are
   standard library vs user code, to prevent false-positive sink candidates.

The catalog is deliberately conservative (precision over recall).  Each entry
maps an unsafe function name to its risk category, severity, and a safer
alternative that the fuzzing harness would typically exercise instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. Vulnerability pattern catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VulnPattern:
    """A single unsafe-API vulnerability pattern."""
    function: str
    category: str  # buffer_overflow | format_string | integer_overflow | use_after_free | command_injection | info_leak | null_deref | double_free
    severity: str  # critical | high | medium
    safe_alternative: str
    description: str


# Key unsafe C/C++ functions organized by vulnerability category.
# Sources: CWE-120, CWE-119, CWE-134, CWE-78, MITRE ATT&CK, ASAN/MSAN docs.
VULN_PATTERNS: Dict[str, VulnPattern] = {
    # --- Buffer overflow (unbounded writes) ---
    "strcpy": VulnPattern(
        "strcpy", "buffer_overflow", "critical", "strncpy / strlcpy",
        "Unbounded string copy — no size check on destination buffer",
    ),
    "strcat": VulnPattern(
        "strcat", "buffer_overflow", "critical", "strncat / strlcat",
        "Unbounded string concatenation — no size check on destination buffer",
    ),
    "gets": VulnPattern(
        "gets", "buffer_overflow", "critical", "fgets",
        "Reads unlimited input into fixed buffer — impossible to use safely",
    ),
    "sprintf": VulnPattern(
        "sprintf", "buffer_overflow", "critical", "snprintf",
        "Unbounded formatted output — no size check on destination buffer",
    ),
    "vsprintf": VulnPattern(
        "vsprintf", "buffer_overflow", "critical", "vsnprintf",
        "Unbounded variadic formatted output — no size check",
    ),
    # --- Buffer overflow (bounded but error-prone) ---
    "strncpy": VulnPattern(
        "strncpy", "buffer_overflow", "medium", "strlcpy / snprintf",
        "Bounded copy but does NOT null-terminate if source >= n; common off-by-one",
    ),
    "strncat": VulnPattern(
        "strncat", "buffer_overflow", "medium", "strlcat",
        "Bounded concatenation but size parameter is remaining space, not buffer size",
    ),
    # --- Format string ---
    "printf": VulnPattern(
        "printf", "format_string", "high", "puts / fputs",
        "User-controlled format string enables arbitrary memory read/write",
    ),
    "fprintf": VulnPattern(
        "fprintf", "format_string", "high", "fputs",
        "User-controlled format string to file stream",
    ),
    # --- Integer overflow leading to buffer overflow ---
    "realloc": VulnPattern(
        "realloc", "integer_overflow", "medium", "reallocarray",
        "Size overflow in realloc can allocate too-small buffer",
    ),
    "malloc": VulnPattern(
        "malloc", "integer_overflow", "medium", "calloc / reallocarray",
        "Size overflow in malloc can allocate too-small buffer",
    ),
    "calloc": VulnPattern(
        "calloc", "integer_overflow", "low", "calloc (with overflow check)",
        "nmemb * size overflow in calloc; most implementations check internally",
    ),
    # --- Use-after-free / double-free ---
    "free": VulnPattern(
        "free", "use_after_free", "medium", "(careful lifecycle management)",
        "Double-free or use-after-free if pointer not set to NULL after free",
    ),
    # --- Memory operations (bounded but risky without proper size) ---
    "memcpy": VulnPattern(
        "memcpy", "buffer_overflow", "high", "memmove / memcpy_s",
        "Size parameter may be incorrect or attacker-controlled; overlapping regions undefined",
    ),
    "memmove": VulnPattern(
        "memmove", "buffer_overflow", "medium", "memmove_s",
        "Safer than memcpy for overlap but size parameter still attacker-controlled",
    ),
    # --- Command injection ---
    "system": VulnPattern(
        "system", "command_injection", "critical", "execvp / posix_spawn",
        "Shell command injection via user-controlled string",
    ),
    "popen": VulnPattern(
        "popen", "command_injection", "critical", "pipe + fork + execvp",
        "Shell command injection via user-controlled string",
    ),
}

# Severity ranking for scoring
_SEVERITY_SCORE = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def get_vuln_pattern(func_name: str) -> Optional[VulnPattern]:
    """Look up a vulnerability pattern by function name (case-sensitive)."""
    return VULN_PATTERNS.get(func_name)


def is_unsafe_function(func_name: str) -> bool:
    """True if func_name is a known-unsafe C/C++ API function."""
    return func_name in VULN_PATTERNS


def vuln_risk_score(func_name: str) -> int:
    """Return risk score (0-4) for a function. 0 = not in catalog."""
    pattern = VULN_PATTERNS.get(func_name)
    if pattern is None:
        return 0
    return _SEVERITY_SCORE.get(pattern.severity, 0)


# ---------------------------------------------------------------------------
# 2. Stdlib / builtin classification
# ---------------------------------------------------------------------------

# Bare libc free-function names — near-exclusively libc, rarely user-defined.
# When the project does NOT define a same-named function, these are stdlib.
# Source: tree-sitter-analyzer synapse_resolver _c_constants.py
LIBC_FUNCTIONS: FrozenSet[str] = frozenset(
    {
        # allocation
        "malloc", "calloc", "realloc",
        # formatted IO
        "printf", "fprintf", "sprintf", "snprintf", "vsnprintf",
        # memory ops
        "memcpy", "memmove", "memset", "memcmp",
        # string ops
        "strlen", "strcmp", "strncmp", "strcpy", "strncpy",
        "strcat", "strncat", "strdup", "strchr", "strrchr", "strstr",
        # stdio file ops
        "fopen", "fclose", "fread", "fwrite", "fseek", "ftell",
        "fgets", "fputs", "fflush",
    }
)

# C++ stdlib namespace prefixes — reliable stdlib signal
STDLIB_NAMESPACE_PREFIXES: Tuple[str, ...] = ("std::", "__gnu_cxx::", "__cxx11::")


def is_libc_function(name: str) -> bool:
    """True when name is a conservatively-classified libc free function.

    The caller is responsible for the project-ownership shadow gate:
    a project-defined C function of the same name must win over this
    classification.
    """
    if not name:
        return False
    return name in LIBC_FUNCTIONS


def is_stdlib_qualified(qualifier: str) -> bool:
    """True when qualifier starts with a C++ stdlib namespace prefix."""
    if not qualifier:
        return False
    return qualifier.startswith(STDLIB_NAMESPACE_PREFIXES)


def classify_call(func_name: str, *, project_has_definition: bool = False) -> str:
    """Classify a call site as 'stdlib', 'vuln_stdlib', or 'project'.

    Returns:
        'stdlib' — safe libc function (no vuln pattern)
        'vuln_stdlib' — libc function with known vulnerability pattern
        'project' — user code or unknown
    """
    if project_has_definition:
        return "project"
    if func_name in LIBC_FUNCTIONS:
        if func_name in VULN_PATTERNS:
            return "vuln_stdlib"
        return "stdlib"
    return "project"


# ---------------------------------------------------------------------------
# 3. Risk signal generation for the indexer
# ---------------------------------------------------------------------------

def risk_signals_for_call(func_name: str, *, caller: str = "", location: str = "") -> List[Dict]:
    """Generate risk signal dicts for a call to func_name.

    Returns a list (0-2 elements) of risk signal dicts compatible with
    the analysis RiskSignal IR.  Each dict has keys:
        signal_type, severity, function, description, safe_alternative
    """
    results = []
    pattern = VULN_PATTERNS.get(func_name)
    if pattern is not None:
        results.append({
            "signal_type": "unsafe_api",
            "severity": pattern.severity,
            "function": func_name,
            "description": pattern.description,
            "safe_alternative": pattern.safe_alternative,
        })
    # Additional: if it's a libc function that the project might shadow
    # (e.g. project defines its own malloc wrapper), that's not a risk
    # signal but worth noting for classification
    return results


# ---------------------------------------------------------------------------
# 4. Crash-type → sink keyword hints (derived from GT analysis)
# ---------------------------------------------------------------------------

# Maps ASAN crash types to keyword boosts for navigation scoring and depth nudges.
# Derived from 1,368-row GT CSV analysis of sink function name patterns.
# Keyword values are additive boosts to _navigation_rows() scores.

CRASH_TYPE_SINK_HINTS: Dict[str, Dict[str, Any]] = {
    "heap-buffer-overflow": {
        "keywords": {"read": 0.08, "get": 0.08, "parse": 0.08, "decode": 0.06,
                     "process": 0.06, "dissect": 0.06, "compress": 0.05},
        "vuln_categories": {"buffer_overflow": 0.12},
        "hint": "Focus on input-consuming code — read/parse/decode paths.",
    },
    "use-of-uninitialized-value": {
        "keywords": {"check": 0.12, "match": 0.10, "compare": 0.10, "branch": 0.08,
                     "hash": 0.07, "serialize": 0.07, "init": 0.08, "row": 0.06,
                     "set": 0.04, "validate": 0.06, "verify": 0.06},
        "vuln_categories": {},
        "hint": "Focus on check/init/validate functions — uninit values propagate to these.",
    },
    "heap-use-after-free": {
        "keywords": {"free": 0.10, "destroy": 0.08, "release": 0.06,
                     "drop": 0.06, "unref": 0.06, "erase": 0.05, "clear": 0.05,
                     "realloc": 0.05, "finalize": 0.06, "cleanup": 0.06, "remove": 0.04},
        "vuln_categories": {"use_after_free": 0.12},
        "hint": "Focus on deallocation code — free/destroy/release paths.",
    },
    "heap-double-free": {
        "keywords": {"free": 0.18, "destroy": 0.10, "cleanup": 0.08, "alloc": 0.04},
        "vuln_categories": {"double_free": 0.15, "use_after_free": 0.10},
        "hint": "The crash function very likely has 'free' in its name (72.7% of cases).",
    },
    "index-out-of-bounds": {
        "keywords": {"decode": 0.12, "parse": 0.10, "index": 0.08, "process": 0.06},
        "vuln_categories": {},
        "hint": "Focus on decode/parse functions with indexing logic.",
    },
    "stack-buffer-overflow": {
        "keywords": {"get": 0.08, "read": 0.08, "parse": 0.06, "check": 0.06, "decode": 0.05},
        "vuln_categories": {"buffer_overflow": 0.10},
        "hint": "Focus on functions with local arrays that read input.",
    },
    "global-buffer-overflow": {
        "keywords": {"parse": 0.10, "dissect": 0.10, "lookup": 0.06, "decode": 0.05},
        "vuln_categories": {},
        "hint": "Focus on parse/dissect functions accessing global lookup tables.",
    },
    "segv": {
        "keywords": {"lookup": 0.08, "get": 0.07, "dispatch": 0.07, "cast": 0.06,
                     "callback": 0.06, "operator": 0.05, "next": 0.05,
                     "access": 0.06, "deref": 0.06, "read": 0.05, "write": 0.05},
        "vuln_categories": {"null_deref": 0.08},
        "hint": "Focus on pointer dereference and memory access paths.",
    },
}

# Entry-point function names that should never be treated as real crash sinks.
ENTRY_POINT_NAMES: FrozenSet[str] = frozenset({"main", "LLVMFuzzerTestOneInput"})

# ---------------------------------------------------------------------------
# 5. Crash-type mental model — prompt templates for expert search guidance
# ---------------------------------------------------------------------------

# These are NOT rule-based filters. They are prompt templates that give the
# LLM the expert's "心理预期操作" (expected dangerous operations) framework.
# The LLM applies these to the specific codebase — we don't pre-filter.

CRASH_TYPE_MENTAL_MODEL: Dict[str, Dict[str, str]] = {
    "heap-use-after-free": {
        "mental_model": "An object is freed, then accessed again without null-check.",
        "expected_ops": "free, delete, realloc, resize, unref, release",
        "search_tip": "The crash site is the *reuse*, not the free. Find both: where it's freed AND where it's accessed after.",
        "forward_keywords": "free, delete, realloc, resize, unref, release, destroy, cleanup",
        "backward_keywords": "access, read, write, deref, use, get, check",
    },
    "heap-buffer-overflow": {
        "mental_model": "A buffer is accessed beyond its allocated size — missing or incorrect length check.",
        "expected_ops": "memcpy, memmove, read, write, strcpy, strncpy, copy, append",
        "search_tip": "Find buffer operations, then verify length checks are missing or bypassed.",
        "forward_keywords": "read, parse, decode, get, process, dissect, compress, memcpy",
        "backward_keywords": "size, length, bounds, check, limit, max, count",
    },
    "heap-double-free": {
        "mental_model": "The same pointer is freed twice — often via error-path cleanup that doesn't null the pointer.",
        "expected_ops": "free, delete, cleanup, destroy",
        "search_tip": "The crash function very likely has 'free' in its name. Find where the pointer is freed and check if it's freed again on error paths.",
        "forward_keywords": "free, destroy, cleanup, release, dealloc, dispose",
        "backward_keywords": "error, fail, exception, abort, cleanup, finally",
    },
    "use-of-uninitialized-value": {
        "mental_model": "A variable is used before being initialized — common in struct fields, switch defaults, and conditional branches.",
        "expected_ops": "check, validate, compare, branch",
        "search_tip": "Focus on check/validate functions — uninit values propagate to these. The crash is in the *check*, not the *init*.",
        "forward_keywords": "check, init, set, validate, verify, test, compare",
        "backward_keywords": "alloc, new, create, declare, default",
    },
    "index-out-of-bounds": {
        "mental_model": "An array index exceeds the valid range — often from unchecked loop bounds or parsed size fields.",
        "expected_ops": "index, access, array, subscript, at",
        "search_tip": "Find decode/parse functions with indexing logic. Check if the index is bounded by a size from input.",
        "forward_keywords": "decode, parse, index, process, get, read, at, access",
        "backward_keywords": "size, count, length, max, limit, bound",
    },
    "stack-buffer-overflow": {
        "mental_model": "A stack-allocated buffer is overflowed — typically a local array written beyond its size.",
        "expected_ops": "read, get, parse, scanf, sprintf",
        "search_tip": "Focus on functions with local arrays (char buf[N]) that read input. The buffer size is often hardcoded.",
        "forward_keywords": "get, read, parse, check, decode, scanf, sprintf",
        "backward_keywords": "size, length, sizeof, stack, local, buf",
    },
    "global-buffer-overflow": {
        "mental_model": "A global/static buffer is accessed out of bounds — common in lookup tables and static arrays.",
        "expected_ops": "parse, dissect, lookup, access, index",
        "search_tip": "Focus on parse/dissect functions accessing global lookup tables. The index may come from parsed input.",
        "forward_keywords": "parse, dissect, lookup, decode, read, get, table",
        "backward_keywords": "size, count, index, offset, entry, global",
    },
    "segv": {
        "mental_model": "Null pointer dereference or invalid memory access — the pointer may be NULL, freed, or uninitialized.",
        "expected_ops": "access, deref, read, write, get",
        "search_tip": "Focus on pointer dereference paths. Check if the pointer can be NULL when dereferenced.",
        "forward_keywords": "access, deref, read, write, get, use, check",
        "backward_keywords": "null, ptr, pointer, alloc, create, find, lookup",
    },
}

# Known ASAN crash type strings for normalization
_CRASH_TYPE_ALIASES: Dict[str, str] = {
    "heap-buffer-overflow": "heap-buffer-overflow",
    "heap_use_after_free": "heap-use-after-free",
    "heap-use-after-free": "heap-use-after-free",
    "use-after-free": "heap-use-after-free",
    "use_of_uninitialized_value": "use-of-uninitialized-value",
    "use-of-uninitialized-value": "use-of-uninitialized-value",
    "uninitialized-value": "use-of-uninitialized-value",
    "heap_double_free": "heap-double-free",
    "heap-double-free": "heap-double-free",
    "double-free": "heap-double-free",
    "index-out-of-bounds": "index-out-of-bounds",
    "index_out_of_bounds": "index-out-of-bounds",
    "stack-buffer-overflow": "stack-buffer-overflow",
    "stack_buffer_overflow": "stack-buffer-overflow",
    "global-buffer-overflow": "global-buffer-overflow",
    "global_buffer_overflow": "global-buffer-overflow",
    "segv": "segv",
    "sigsegv": "segv",
    "signal-11": "segv",
    "dynamic-stack-buffer-overflow": "dynamic-stack-buffer-overflow",
    "stack-buffer-underflow": "stack-buffer-underflow",
    "heap-buffer-underflow": "heap-buffer-underflow",
    "global-buffer-underflow": "global-buffer-underflow",
    "use-after-poison": "use-after-poison",
    "stack-use-after-return": "stack-use-after-return",
    "stack-use-after-scope": "stack-use-after-scope",
    "invalid-free": "invalid-free",
    "attempting-free": "invalid-free",
    "bad-free": "invalid-free",
    "negative-size-param": "negative-size-param",
    "negative-size": "negative-size-param",
    "memcpy-param-overlap": "memcpy-param-overlap",
    "memory-ranges-overlap": "memcpy-param-overlap",
    "container-overflow": "container-overflow",
    "object-size": "object-size",
    "null-dereference": "null-dereference",
    "null-pointer-dereference": "null-dereference",
    "bad-cast": "bad-cast",
    "function-pointer": "invalid-function-pointer",
    "invalid-function-pointer": "invalid-function-pointer",
    "undefined-behavior": "ubsan",
    "ubsan": "ubsan",
    "assert": "assertion-failure",
    "assertion": "assertion-failure",
    "assertion-failure": "assertion-failure",
    "abort": "abort",
    "unknown": "unknown",
    "unset": "unknown",
}


def normalize_crash_type(raw: str) -> str:
    """Normalize sanitizer/description spelling without inventing a cause.

    The returned taxonomy is shared by description priors, submit feedback,
    and offline evaluation. Unknown input remains ``unknown`` rather than
    being coerced into a memory-corruption family.
    """
    key = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
    if not key:
        return "unknown"
    direct = _CRASH_TYPE_ALIASES.get(key)
    if direct:
        return direct
    # Sanitizer summaries often append an address, interceptor, or prose.
    # Longest-first matching avoids classifying stack-use-after-return as a
    # generic use-after-free-like phrase.
    for alias in sorted(_CRASH_TYPE_ALIASES, key=len, reverse=True):
        if alias in key:
            return _CRASH_TYPE_ALIASES[alias]
    return key[:120]


def is_entry_point_function(func_name: str) -> bool:
    """True if func_name is an entry-point function (main, LLVMFuzzerTestOneInput)."""
    return func_name.strip() in ENTRY_POINT_NAMES


__all__ = [
    "VulnPattern",
    "VULN_PATTERNS",
    "LIBC_FUNCTIONS",
    "STDLIB_NAMESPACE_PREFIXES",
    "get_vuln_pattern",
    "is_unsafe_function",
    "is_libc_function",
    "is_stdlib_qualified",
    "classify_call",
    "risk_signals_for_call",
    "vuln_risk_score",
    "CRASH_TYPE_SINK_HINTS",
    "CRASH_TYPE_MENTAL_MODEL",
    "ENTRY_POINT_NAMES",
    "normalize_crash_type",
    "is_entry_point_function",
]
