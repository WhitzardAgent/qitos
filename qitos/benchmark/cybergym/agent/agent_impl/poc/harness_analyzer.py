"""Selected-harness input consumption analysis.

This module is deliberately conservative.  It uses the existing C/C++ parser
and function-extraction path to locate the selected entry, then derives a
compact typed model from the entry body.  A partial result is useful: unknown
or unresolved first hops are navigation gaps, not evidence that no path exists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from ...analysis.parser import Parser
from ...analysis.function_extraction import walk_tree
from ...state import HarnessConsumptionEvidence, HarnessConsumptionModel


_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm"}
_IGNORED_CALLS = {
    "LLVMFuzzerTestOneInput", "main",
    "malloc", "calloc", "realloc", "free", "memset", "memcpy", "memmove",
    "memcmp", "strncmp", "strcmp", "strlen", "sizeof",
    "fopen", "fdopen", "fclose", "fwrite", "write", "open", "close",
    "tmpfile", "mkstemp", "unlink", "remove",
    "printf", "fprintf", "snprintf", "sprintf", "puts", "fputs",
    "assert", "__assert_fail", "abort", "exit",
}
_LOGGING_PREFIXES = ("log", "trace", "debug", "warn", "error", "fprintf", "printf")


def analyze_harness_consumption(
    repo_root: Path,
    source_path: str,
    entry_function: str,
) -> HarnessConsumptionModel:
    """Analyze how one selected harness entry consumes fuzzer input."""
    repo_root = Path(repo_root)
    rel = str(source_path or "").lstrip("./")
    path = (repo_root / rel).resolve()
    model = HarnessConsumptionModel(status="unresolved")
    if not rel or not path.is_file():
        model.status = "partial"
        model.evidence.append(HarnessConsumptionEvidence(
            "gap", f"harness source not found: {rel}", rel, 0, .0,
        ))
        return model

    language = _language_from_suffix(path.suffix)
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = Parser().parse_file(path, language)
    used_fallback = False
    if parsed is None:
        used_fallback = True
        definitions, calls = _fallback_definitions_calls(text)
    else:
        definitions, calls = walk_tree(parsed.root, _source_text(parsed.source), language, parsed._line_table)
    matches = [d for d in definitions if str(d.get("name") or "") == entry_function]
    if len(matches) != 1:
        model.status = "partial"
        model.evidence.append(HarnessConsumptionEvidence(
            "gap",
            f"entry definition count for {entry_function}: {len(matches)}",
            rel,
            int(matches[0].get("start_line") or 0) if matches else 0,
            .2,
        ))
        return model

    entry = matches[0]
    start_line = int(entry.get("start_line") or 1)
    end_line = int(entry.get("end_line") or start_line)
    lines = text.splitlines()
    body = "\n".join(lines[max(0, start_line - 1): min(len(lines), end_line)])
    signature = _signature_prefix(lines, start_line)
    data_param, size_param = _extract_data_size_params(signature)
    model.data_parameter = data_param
    model.size_parameter = size_param

    repo_symbols = _collect_repo_function_names(repo_root)
    entry_calls = [
        c for c in calls
        if start_line <= int(c.get("line") or 0) <= end_line
    ]
    first_hops: list[str] = []
    unresolved = 0
    resolved = 0
    for call in sorted(entry_calls, key=lambda c: int(c.get("line") or 0)):
        name = _clean_call_name(str(call.get("name") or call.get("full_name") or ""))
        if not name or _ignore_call(name):
            continue
        if name not in first_hops:
            first_hops.append(name)
        if name in repo_symbols:
            resolved += 1
        else:
            unresolved += 1
        if len(first_hops) >= 12:
            break
    model.first_hops = first_hops
    model.first_hop_resolution = {
        "resolved": resolved,
        "ambiguous": 0,
        "unresolved": unresolved,
    }
    for hop in first_hops[:3]:
        line = _first_call_line(entry_calls, hop, start_line)
        model.evidence.append(HarnessConsumptionEvidence(
            "first_hop",
            hop,
            rel,
            line,
            .82 if hop in repo_symbols else .48,
        ))

    patterns: list[str] = []
    if data_param and size_param and _has_direct_data_size_call(body, data_param, size_param, first_hops):
        patterns.append("direct_data_size")
        model.evidence.append(HarnessConsumptionEvidence(
            "direct_data_size",
            f"{data_param} and {size_param} passed to direct callee",
            rel,
            _first_regex_line(lines, start_line, end_line, rf"\b{re.escape(data_param)}\b.*\b{re.escape(size_param)}\b"),
            .86,
        ))

    magic, magic_line, magic_expr = _extract_magic(body, lines, start_line, end_line, data_param)
    if magic:
        patterns.append("magic_header")
        model.magic_bytes = magic
        model.evidence.append(HarnessConsumptionEvidence(
            "magic_header", magic_expr, rel, magic_line, .88,
        ))

    split_expr, split_line = _extract_split(body, lines, start_line, end_line, data_param)
    if split_expr:
        patterns.append("struct_split")
        model.evidence.append(HarnessConsumptionEvidence(
            "struct_split", split_expr, rel, split_line, .76,
        ))

    temp_api, temp_line = _extract_temp_file_api(body, lines, start_line, end_line)
    if temp_api:
        patterns.append("temp_file")
        model.temp_file_api = temp_api
        model.evidence.append(HarnessConsumptionEvidence(
            "temp_file", temp_api, rel, temp_line, .82,
        ))

    selector, selector_line = _extract_selector(body, lines, start_line, end_line, data_param, first_hops)
    if selector:
        patterns.append("multi_api")
        model.selector_expression = selector
        model.evidence.append(HarnessConsumptionEvidence(
            "multi_api", selector, rel, selector_line, .72,
        ))

    model.patterns = list(dict.fromkeys(patterns)) or ["unknown"]
    model.pattern = next((p for p in model.patterns if p != "unknown"), "unknown")
    if parsed is not None and parsed.has_error:
        model.status = "partial"
    elif unresolved:
        model.status = "partial"
    else:
        model.status = "success" if model.pattern != "unknown" or first_hops else "partial"
    if used_fallback and model.status == "success":
        model.evidence.append(HarnessConsumptionEvidence(
            "parser_fallback",
            "tree-sitter parser unavailable; used conservative source fallback",
            rel,
            start_line,
            .35,
        ))
    if model.status == "partial" and not any(ev.kind == "gap" for ev in model.evidence):
        model.evidence.append(HarnessConsumptionEvidence(
            "gap",
            "harness consumption model is partial; unresolved calls remain possible path anchors",
            rel,
            start_line,
            .35,
        ))
    return model


def _language_from_suffix(suffix: str) -> str:
    return "cpp" if suffix.lower() in {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".mm"} else "c"


def _source_text(source: Any) -> str:
    return source.decode("utf-8", errors="replace") if isinstance(source, bytes) else str(source or "")


def _signature_prefix(lines: list[str], start_line: int) -> str:
    collected: list[str] = []
    for line in lines[max(0, start_line - 4): start_line + 2]:
        collected.append(line.strip())
        if "{" in line:
            break
    return " ".join(collected)


def _split_params(params: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in params:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _extract_data_size_params(signature: str) -> tuple[str, str]:
    match = re.search(r"\((.*)\)", signature)
    if not match:
        return "", ""
    params = _split_params(match.group(1))
    data = ""
    size = ""
    for param in params:
        cleaned = re.sub(r"\s+", " ", param.strip())
        name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?$", cleaned)
        name = name_match.group(1) if name_match else ""
        lowered = cleaned.lower()
        if not data and ("*" in cleaned or "[]" in cleaned) and re.search(r"(uint8_t|char|void|byte|unsigned char|std::byte)", lowered):
            data = name
        if not size and re.search(r"\b(size_t|int|unsigned|uint\d+_t|long)\b", lowered) and re.search(r"(size|len|length|nbytes|sz)", name, re.I):
            size = name
    if not data and params:
        first = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?$", params[0].strip())
        data = first.group(1) if first else ""
    if not size and len(params) > 1:
        second = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?$", params[1].strip())
        size = second.group(1) if second else ""
    return data, size


def _collect_repo_function_names(repo_root: Path) -> set[str]:
    names: set[str] = set()
    pattern = re.compile(r"^\s*(?:static\s+|extern\s+|inline\s+|constexpr\s+|[\w:<>,~*&\s]+\s+)+([A-Za-z_~][A-Za-z0-9_:~]*)\s*\([^;{}]*\)\s*\{", re.M)
    for path in repo_root.rglob("*"):
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(text[:512_000]):
            names.add(match.group(1).split("::")[-1])
        if len(names) > 5000:
            break
    return names


def _clean_call_name(name: str) -> str:
    name = re.sub(r"\s+", "", name)
    name = name.split("<", 1)[0]
    return name.split("::")[-1].split(".")[-1].split("->")[-1]


def _ignore_call(name: str) -> bool:
    lowered = name.lower()
    return name in _IGNORED_CALLS or any(lowered.startswith(p) for p in _LOGGING_PREFIXES)


def _first_call_line(calls: list[dict[str, Any]], name: str, default: int) -> int:
    for call in calls:
        if _clean_call_name(str(call.get("name") or call.get("full_name") or "")) == name:
            return int(call.get("line") or default)
    return default


def _first_regex_line(lines: list[str], start_line: int, end_line: int, pattern: str) -> int:
    regex = re.compile(pattern)
    for idx in range(max(0, start_line - 1), min(len(lines), end_line)):
        if regex.search(lines[idx]):
            return idx + 1
    return start_line


def _has_direct_data_size_call(body: str, data: str, size: str, first_hops: list[str]) -> bool:
    if not data or not size:
        return False
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^;{}]*)\)")
    for match in call_re.finditer(body):
        callee = _clean_call_name(match.group(1))
        if first_hops and callee not in first_hops:
            continue
        args = match.group(2)
        if re.search(rf"\b{re.escape(data)}\b", args) and re.search(rf"\b{re.escape(size)}\b", args):
            return True
    return False


def _decode_literal_to_hex(literal: str) -> str:
    try:
        value = ast.literal_eval(literal)
        if isinstance(value, str):
            raw = value.encode("latin1", errors="ignore")
        elif isinstance(value, bytes):
            raw = value
        else:
            return ""
        return " ".join(f"{b:02X}" for b in raw[:16])
    except Exception:
        return ""


def _extract_magic(body: str, lines: list[str], start_line: int, end_line: int, data: str) -> tuple[str, int, str]:
    data_pat = re.escape(data or "data")
    memcmp = re.search(rf"\b(?:memcmp|strncmp)\s*\([^,]*\b{data_pat}\b[^,]*,\s*((?:u8)?\"(?:\\.|[^\"])+\"|\'(?:\\.|[^\'])+\')\s*,\s*(\d+)", body)
    if memcmp:
        magic = _decode_literal_to_hex(memcmp.group(1))
        if magic:
            expr = memcmp.group(0)[:120]
            return magic, _first_regex_line(lines, start_line, end_line, re.escape(memcmp.group(0).split("(", 1)[0])), expr
    comparisons = re.findall(rf"\b{data_pat}\s*\[\s*(\d+)\s*\]\s*==\s*(0x[0-9A-Fa-f]+|\d+|'(?:\\.|[^'])')", body)
    if comparisons:
        ordered = sorted((int(idx), val) for idx, val in comparisons if int(idx) < 16)
        if ordered and ordered[0][0] == 0:
            bytes_out = []
            for idx, val in ordered:
                if idx != len(bytes_out):
                    break
                if val.startswith("'"):
                    decoded = _decode_literal_to_hex(val)
                    if not decoded:
                        break
                    bytes_out.append(decoded.split()[0])
                else:
                    bytes_out.append(f"{int(val, 0) & 0xFF:02X}")
            if bytes_out:
                expr = f"{data}[0..{len(bytes_out) - 1}] fixed byte comparisons"
                return " ".join(bytes_out), _first_regex_line(lines, start_line, end_line, rf"\b{data_pat}\s*\["), expr
    return "", 0, ""


def _extract_split(body: str, lines: list[str], start_line: int, end_line: int, data: str) -> tuple[str, int]:
    if not data:
        return "", 0
    data_pat = re.escape(data)
    patterns = [
        rf"\b{data_pat}\s*\[\s*(\d+)\s*\]",
        rf"\b{data_pat}\s*\+\s*(\d+)",
    ]
    for pattern in patterns:
        offsets = [int(x) for x in re.findall(pattern, body) if int(x) > 0]
        if len(set(offsets)) >= 1:
            expr = f"{data} accessed at offsets " + ", ".join(str(x) for x in sorted(set(offsets))[:4])
            return expr, _first_regex_line(lines, start_line, end_line, pattern)
    return "", 0


def _extract_temp_file_api(body: str, lines: list[str], start_line: int, end_line: int) -> tuple[str, int]:
    match = re.search(r"\b(tmpfile|mkstemp|fopen|open|fwrite|write)\s*\(", body)
    if not match:
        return "", 0
    return match.group(1), _first_regex_line(lines, start_line, end_line, rf"\b{match.group(1)}\s*\(")


def _extract_selector(
    body: str,
    lines: list[str],
    start_line: int,
    end_line: int,
    data: str,
    first_hops: list[str],
) -> tuple[str, int]:
    if len(first_hops) < 2:
        return "", 0
    data_pat = re.escape(data or "data")
    switch = re.search(rf"\bswitch\s*\(([^)]*\b{data_pat}\b[^)]*)\)", body)
    if switch:
        return switch.group(1).strip(), _first_regex_line(lines, start_line, end_line, r"\bswitch\s*\(")
    ifs = re.findall(rf"\bif\s*\(([^)]*\b{data_pat}\b[^)]*)\)", body)
    if len(ifs) >= 2:
        return ifs[0].strip(), _first_regex_line(lines, start_line, end_line, r"\bif\s*\(")
    return "", 0
