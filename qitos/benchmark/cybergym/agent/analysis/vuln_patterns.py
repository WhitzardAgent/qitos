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
        "keywords": {"check": 0.12, "init": 0.08, "set": 0.04,
                     "validate": 0.06, "verify": 0.06},
        "vuln_categories": {},
        "hint": "Focus on check/init/validate functions — uninit values propagate to these.",
    },
    "heap-use-after-free": {
        "keywords": {"free": 0.10, "destroy": 0.08, "release": 0.06,
                     "finalize": 0.06, "cleanup": 0.06, "remove": 0.04},
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
        "keywords": {"access": 0.06, "deref": 0.06, "read": 0.05, "write": 0.05, "get": 0.04},
        "vuln_categories": {"null_deref": 0.08},
        "hint": "Focus on pointer dereference and memory access paths.",
    },
}

# Entry-point function names that should never be treated as real crash sinks.
ENTRY_POINT_NAMES: FrozenSet[str] = frozenset({"main", "LLVMFuzzerTestOneInput"})

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
}


def normalize_crash_type(raw: str) -> str:
    """Normalize a raw crash type string to a canonical key in CRASH_TYPE_SINK_HINTS."""
    key = raw.strip().lower().replace(" ", "-")
    return _CRASH_TYPE_ALIASES.get(key, key)


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
    "ENTRY_POINT_NAMES",
    "normalize_crash_type",
    "is_entry_point_function",
]
