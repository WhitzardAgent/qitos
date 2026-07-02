from __future__ import annotations

import re
from typing import Any, Dict, List

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
_FILE_RE = re.compile(
    r"\b[\w./-]+\.(?:c|cc|cpp|cxx|h|hpp|rs|go|java|py|js|ts|png|jpg|gif|pdf|xml|json|yaml|yml|bin)\b"
)
_SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")

_MEMORY_TERMS = (
    "heap-buffer-overflow",
    "stack-buffer-overflow",
    "use-after-free",
    "double free",
    "out-of-bounds",
    "buffer overflow",
)

_PARSER_TERMS = ("parser", "parse", "decode", "reader", "chunk", "header")

_SIGNAL_PATTERNS = {
    "ASAN": ("asan", "addresssanitizer", "heap-buffer-overflow", "stack-buffer-overflow"),
    "UBSAN": ("ubsan", "undefinedbehaviorsanitizer", "undefined behavior"),
    "MSAN": ("msan", "memorysanitizer"),
    "CRASH": ("crash", "segmentation fault", "segfault", "assertion"),
}

_PATH_HINT_PREFIXES = (
    "src/",
    "lib/",
    "app/",
    "test/",
    "tests/",
    "fuzz/",
    "oss-fuzz/",
)


def _uniq(items: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _detect_signal(text: str) -> str:
    lowered = text.lower()
    for label, patterns in _SIGNAL_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return label
    return "unknown"


def _detect_vulnerability_class(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in _MEMORY_TERMS):
        return "memory-safety"
    if any(term in lowered for term in _PARSER_TERMS):
        return "parser"
    return "unknown"


def _input_hints(text: str, harness_info: str) -> List[str]:
    lowered = f"{text} {harness_info}".lower()
    hints: List[str] = []
    if "stdin" in lowered:
        hints.append("stdin")
    if any(token in lowered for token in ("argv", "argument", "path", "file")):
        hints.append("file")
    for ext in (".png", ".jpg", ".gif", ".pdf", ".xml", ".json", ".yaml", ".yml", ".bin"):
        if ext in lowered:
            hints.append(ext)
    return _uniq(hints)


def _source_file_mentions(text: str) -> List[str]:
    matches = [match for match in _FILE_RE.findall(text) if "/" in match or match.startswith(_PATH_HINT_PREFIXES)]
    return _uniq(matches[:12])


def _symbol_mentions(text: str) -> List[str]:
    blocked = {
        "crash",
        "parser",
        "binary",
        "file",
        "asan",
        "ubsan",
        "msan",
        "heap",
        "stack",
        "crafted",
        "under",
        "when",
        "with",
        "malformed",
    }
    candidates = [token for token in _SYMBOL_RE.findall(text) if token.lower() not in blocked]
    return _uniq(candidates[:12])


def _extract_search_anchors(text: str) -> List[str]:
    """Extract searchable code identifiers from vulnerability description."""
    anchors: List[str] = []
    # Function calls: func_name()
    for m in re.finditer(r'([a-zA-Z_]\w+)\s*\(\)', text):
        anchors.append(m.group(1))
    # File references: name.c, name.cpp, etc.
    for m in re.finditer(r'(\w+\.[ch](?:pp|xx)?)\b', text):
        anchors.append(m.group(1))
    # "in the X module/component" pattern
    for m in re.finditer(r'in\s+(?:the\s+)?(\w+)\s+(?:module|component|subsystem)', text, re.IGNORECASE):
        anchors.append(m.group(1))
    # CamelCase identifiers
    for m in re.finditer(r'\b([a-z]+[A-Z][a-zA-Z]+)\b', text):
        anchors.append(m.group(1))
    return _uniq(anchors[:12])


def extract_task_spec_deterministic(
    description: str,
    *,
    error_txt: str = "",
    patch_diff: str = "",
    harness_info: str = "",
) -> Dict[str, Any]:
    combined = "\n".join([description or "", error_txt or "", patch_diff or "", harness_info or ""])
    cve_match = _CVE_RE.search(combined)
    source_files = _source_file_mentions(combined)
    symbols = _symbol_mentions(combined)
    # P47: expanded entrypoint detection prefixes to catch common patterns
    # beyond parse/read/decode (handle_, process_, LLVMFuzzerTestOneInput, etc.)
    likely_entrypoints = [
        token for token in symbols if token.lower().startswith((
            "parse", "read", "decode", "handle", "process", "accept",
            "consume", "transform", "convert", "load", "import",
            "render", "execute", "dispatch", "on_", "do_",
            "llvmfuzzertestoneinput",
        ))
    ][:6]
    likely_fuzz_targets = [path for path in source_files if "fuzz" in path.lower() or "fuzzer" in path.lower()][:6]

    signal = _detect_signal(combined)
    confidence = 0.2
    if cve_match:
        confidence += 0.2
    if source_files:
        confidence += 0.2
    if likely_entrypoints:
        confidence += 0.2
    if signal != "unknown":
        confidence += 0.2

    return {
        "cve_id": cve_match.group(0) if cve_match else "",
        "vulnerability_class": _detect_vulnerability_class(combined),
        "expected_signal": signal,
        "input_vector_hints": _input_hints(description or "", harness_info or ""),
        "likely_entrypoints": likely_entrypoints,
        "likely_fuzz_targets": likely_fuzz_targets,
        "source_files_mentioned": source_files,
        "symbols_mentioned": symbols,
        "task_spec_confidence": max(0.0, min(confidence, 1.0)),
        "search_anchors": _extract_search_anchors(description or ""),
    }


def build_task_spec(
    description: str,
    *,
    error_txt: str = "",
    patch_diff: str = "",
    harness_info: str = "",
) -> Dict[str, Any]:
    return extract_task_spec_deterministic(
        description,
        error_txt=error_txt,
        patch_diff=patch_diff,
        harness_info=harness_info,
    )
