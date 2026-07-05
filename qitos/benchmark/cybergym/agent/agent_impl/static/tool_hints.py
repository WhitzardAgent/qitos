"""Static-analysis-aware hints for classic file navigation tools.

The helpers in this module are intentionally read-only and state-backed.  They
do not turn GREP/READ/GLOB hits into facts; they add short, source-addressable
navigation leads derived from the existing ranked paths, reviewed candidates,
harness metadata, and lightweight repository index.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable


@dataclass
class ToolHint:
    role: str = "unknown"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    path_id: str | None = None
    candidate_id: str | None = None
    family: str | None = None


@dataclass
class AnnotatedHit:
    path: str
    line: int | None = None
    text: str = ""
    score: float = 0.0
    hints: list[ToolHint] = field(default_factory=list)


_ROLE_WEIGHT = {
    "crash_site": 1.00,
    "causal_site": .92,
    "parser_gate": .82,
    "dispatch": .78,
    "entry": .76,
    "path_anchor": .64,
    "seed": .48,
    "wrapper": .36,
    "unknown": .10,
}

_PARSER_TERMS = (
    "parse", "parser", "decode", "decoder", "read", "reader", "load",
    "import", "chunk", "record", "packet", "table", "field", "header",
)
_DISPATCH_TERMS = ("dispatch", "handler", "callback", "operator", "virtual", "switch", "type", "tag")
_SEED_TERMS = ("corpus", "seed", "sample", "testdata", "test-data", "fixtures")
_ENTRY_TERMS = ("llvmfuzzertestoneinput", "fuzz_target", "fuzzer", "harness")


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _clean_path(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    return str(PurePosixPath(value)) if value else ""


def _path_variants(path: str) -> set[str]:
    clean = _clean_path(path)
    variants = {clean}
    if clean.startswith("repo-vul/"):
        variants.add(clean[len("repo-vul/"):])
    elif clean:
        variants.add("repo-vul/" + clean)
    return {item for item in variants if item and item != "."}


def _same_path(left: str, right: str) -> bool:
    return bool(_path_variants(left) & _path_variants(right))


def _leaf_function(name: str) -> str:
    return str(name or "").rsplit("::", 1)[-1]


def _function_role(name: str, fallback: str = "wrapper") -> str:
    lowered = _leaf_function(name).casefold()
    if any(term in lowered for term in _ENTRY_TERMS):
        return "entry"
    if any(term in lowered for term in _DISPATCH_TERMS):
        return "dispatch"
    if any(term in lowered for term in _PARSER_TERMS):
        return "parser_gate"
    return fallback


def _candidate_role(candidate: Any) -> str:
    metadata = _value(candidate, "metadata", {}) or {}
    role = str(_value(metadata, "candidate_role", "") or "")
    if role:
        return role
    status = str(_value(candidate, "status", "") or "")
    return "crash_site" if status == "confirmed" else "path_anchor"


def _description_terms(state: Any) -> set[str]:
    analysis = _value(state, "description_analysis", None)
    values: list[str] = []
    for field_name in (
        "suspect_functions", "suspect_files", "suspect_modules",
        "described_operations", "search_hints", "mechanism_tags",
    ):
        raw = _value(analysis, field_name, []) if analysis is not None else []
        if isinstance(raw, (list, tuple, set)):
            values.extend(str(item) for item in raw)
    values.extend(str(item) for item in (_value(state, "source_files_mentioned", []) or []))
    terms: set[str] = set()
    for value in values:
        terms.update(
            token.casefold()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value)
            if len(token) >= 3
        )
    return terms


def _selected_harness_ids(state: Any) -> set[str]:
    resolution = _value(state, "harness_resolution", None)
    selected = str(_value(resolution, "selected_candidate_id", "") or "")
    return {selected} if selected else set()


def _path_hints(state: Any, path: str, line: int | None = None) -> list[ToolHint]:
    hints: list[ToolHint] = []
    for ranked in list(_value(state, "ranked_vulnerability_paths", []) or []):
        path_id = str(_value(ranked, "path_id", "") or "")
        family = str(_value(ranked, "candidate_family", "") or "") or None
        endpoint_role = str(_value(ranked, "endpoint_role", "") or "path_anchor")
        chain = list(_value(ranked, "chain", []) or [])
        endpoint = _value(ranked, "endpoint", {}) or {}
        for index, node in enumerate(chain):
            node_file = str(_value(node, "file", "") or "")
            if not _same_path(path, node_file):
                continue
            node_line = int(_value(node, "line", 0) or 0)
            if line and node_line and abs(line - node_line) > 120:
                continue
            function = str(_value(node, "function", "") or "")
            is_endpoint = (
                _same_path(node_file, str(_value(endpoint, "file", "") or ""))
                and _leaf_function(function) == _leaf_function(str(_value(endpoint, "function", "") or ""))
            )
            if is_endpoint or index == len(chain) - 1:
                role = endpoint_role
                reason = f"ranked path {path_id or '?'} endpoint `{_leaf_function(function)}`"
                if role in {"crash_site", "causal_site", "dangerous_primitive"}:
                    next_action = (
                        f"READ this source span, then record_sink_candidate with "
                        f"candidate_role=\"{role}\" and ranked_path_id=\"{path_id}\" if confirmed"
                    )
                else:
                    next_action = "follow the downstream ranked-path node before treating this as the final sink"
            elif index == 0:
                role = "entry"
                reason = f"entry node on ranked path {path_id or '?'}"
                next_action = "inspect first-hop parser/dispatcher and input consumption"
            else:
                role = _function_role(function, "path_anchor")
                reason = f"intermediate `{_leaf_function(function)}` on ranked path {path_id or '?'}"
                next_node = chain[index + 1] if index + 1 < len(chain) else {}
                next_function = _leaf_function(str(_value(next_node, "function", "") or ""))
                next_action = f"follow next ranked-path node `{next_function}`" if next_function else "follow the next callee"
            hints.append(ToolHint(
                role=role,
                confidence=.94 if is_endpoint else .84,
                reasons=[reason],
                next_actions=[next_action],
                path_id=path_id or None,
                family=family,
            ))
    return hints


def _candidate_hints(state: Any, path: str, line: int | None = None) -> list[ToolHint]:
    hints: list[ToolHint] = []
    for candidate in list(_value(state, "sink_candidates", []) or []):
        candidate_file = str(_value(candidate, "file", "") or "")
        if not candidate_file:
            location = str(_value(candidate, "location", "") or "")
            candidate_file = location.rsplit(":", 1)[0] if ":" in location else location
        if not _same_path(path, candidate_file):
            continue
        candidate_line = int(_value(candidate, "line", 0) or 0)
        if line and candidate_line and abs(line - candidate_line) > 120:
            continue
        metadata = _value(candidate, "metadata", {}) or {}
        role = _candidate_role(candidate)
        reviewed = bool(_value(metadata, "reviewed", False))
        path_id = str(_value(metadata, "ranked_path_id", "") or "")
        function = _leaf_function(str(_value(candidate, "function", "") or "candidate"))
        confidence = float(_value(candidate, "confidence", 0.0) or 0.0)
        hints.append(ToolHint(
            role=role,
            confidence=max(.55, min(.98, confidence + (.12 if reviewed else 0))),
            reasons=[f"{'reviewed' if reviewed else 'unreviewed'} sink candidate `{function}`"],
            next_actions=[
                "map controlling input fields and trigger conditions"
                if reviewed and role != "path_anchor"
                else "review source evidence before selecting this candidate"
            ],
            path_id=path_id or None,
            candidate_id=str(_value(candidate, "candidate_id", "") or "") or None,
            family=str(_value(metadata, "candidate_family", "") or _value(candidate, "category", "") or "") or None,
        ))
    return hints


def _chain_hints(state: Any, path: str, line: int | None = None) -> list[ToolHint]:
    hints: list[ToolHint] = []
    role_map = {"parser": "parser_gate", "guard": "parser_gate", "sink": "crash_site"}
    for node in list(_value(state, "call_chain_nodes", []) or []):
        location = str(_value(node, "location", "") or "")
        node_file, _, node_line_text = location.rpartition(":")
        if not _same_path(path, node_file or location):
            continue
        try:
            node_line = int(node_line_text)
        except Exception:
            node_line = 0
        if line and node_line and abs(line - node_line) > 120:
            continue
        raw_role = str(_value(node, "role", "") or "")
        role = role_map.get(raw_role, raw_role if raw_role in _ROLE_WEIGHT else _function_role(str(_value(node, "function", ""))))
        function = _leaf_function(str(_value(node, "function", "") or "chain node"))
        hints.append(ToolHint(
            role=role,
            confidence=.86 if str(_value(node, "status", "")) == "confirmed" else .68,
            reasons=[f"{_value(node, 'status', 'inferred')} call-chain node `{function}`"],
            next_actions=["inspect adjacent chain node or record its gate"],
            candidate_id=str(_value(node, "sink_id", "") or "") or None,
        ))
    return hints


def _harness_hints(state: Any, path: str) -> list[ToolHint]:
    hints: list[ToolHint] = []
    selected_ids = _selected_harness_ids(state)
    for harness in list(_value(state, "harness_candidates", []) or []):
        if not _same_path(path, str(_value(harness, "source_path", "") or "")):
            continue
        candidate_id = str(_value(harness, "candidate_id", "") or "")
        selected = not selected_ids or candidate_id in selected_ids
        function = _leaf_function(str(_value(harness, "entry_function", "") or "harness"))
        hints.append(ToolHint(
            role="entry",
            confidence=.98 if selected else .76,
            reasons=[f"{'selected ' if selected else ''}harness entry `{function}`"],
            next_actions=["READ the entry body and follow its first parser/dispatcher calls"],
            candidate_id=candidate_id or None,
        ))
    return hints


def _repo_index_hint(state: Any, path: str, start: int | None = None, end: int | None = None) -> list[ToolHint]:
    metadata = _value(state, "metadata", {}) or {}
    index = _value(metadata, "repo_index_v2", {}) or {}
    files = _value(index, "files", {}) or {}
    record = next((value for key, value in files.items() if _same_path(path, str(key))), None)
    if not isinstance(record, dict):
        return []
    hints: list[ToolHint] = []
    functions = list(record.get("functions") or [])
    for function in functions:
        fn_start = int(_value(function, "line", 0) or 0)
        fn_end = int(_value(function, "end_line", fn_start) or fn_start)
        if start is not None and end is not None and (fn_end < start or fn_start > end):
            continue
        name = str(_value(function, "name", "") or "")
        role = _function_role(name)
        hints.append(ToolHint(
            role=role,
            confidence=.66,
            reasons=[f"enclosing function `{name}` lines {fn_start}-{fn_end}"],
            next_actions=[
                "follow a concrete callee; this region currently looks like a wrapper"
                if role == "wrapper"
                else "inspect its path checks and downstream operation"
            ],
        ))
        break
    indexed_roles = [str(item) for item in list(record.get("likely_roles") or []) if str(item)]
    parser_terms = [str(item) for item in list(record.get("parser_terms") or []) if str(item)]
    risk_terms = [str(item) for item in list(record.get("risk_terms") or []) if str(item)]
    for role in indexed_roles:
        normalized_role = "path_anchor" if role == "risk_operation" else role
        reasons = [f"repo index role={role}"]
        if parser_terms and normalized_role == "parser_gate":
            reasons.append(f"parser terms: {', '.join(parser_terms[:4])}")
        if risk_terms and role == "risk_operation":
            reasons.append(f"risk terms: {', '.join(risk_terms[:4])}")
        hints.append(ToolHint(
            role=normalized_role if normalized_role in _ROLE_WEIGHT else "unknown",
            confidence=.58,
            reasons=reasons,
            next_actions=[
                "READ the strongest operation and confirm whether it is a crash/causal endpoint"
                if role == "risk_operation"
                else "follow this indexed role into a source-backed path"
            ],
        ))
    return hints


def _lexical_hint(path: str, text: str, family: str = "") -> list[ToolHint]:
    combined = f"{path} {text}".casefold()
    hints: list[ToolHint] = []
    if any(term in combined for term in _ENTRY_TERMS):
        hints.append(ToolHint("entry", .62, ["entry/fuzzer lexical signal"], ["inspect first-hop input consumption"]))
    if any(term in combined for term in _PARSER_TERMS):
        hints.append(ToolHint("parser_gate", .52, ["parser/format lexical signal"], ["inspect length, tag, offset, and format gates"]))
    if any(term in combined for term in _DISPATCH_TERMS):
        hints.append(ToolHint("dispatch", .55, ["dispatch/type-selection lexical signal"], ["follow the selected handler/callee"]))
    if any(term in combined for term in _SEED_TERMS):
        hints.append(ToolHint("seed", .58, ["corpus/seed path signal"], ["inspect a small valid carrier before mutation"]))

    family = str(family or "").casefold()
    risky = False
    role = "crash_site"
    reason = ""
    if family == "uninitialized" and re.search(r"\b(if|switch|compare|memcmp|strcmp|match|hash)\b", combined):
        risky, reason = True, "uninitialized-family consumer/use pattern"
    elif family == "lifetime" and re.search(r"\b(free|delete|destroy|release|unref|erase|clear|realloc)\b", combined):
        risky, role, reason = True, "causal_site", "lifetime invalidation pattern"
    elif family in {"bounds", "integer"} and re.search(r"\b(memcpy|memmove|strcpy|strncpy|alloc|malloc|realloc|resize)\b|\[[^\]]+\]", combined):
        risky, reason = True, f"{family}-family memory/size consumer"
    elif family in {"pointer", "dispatch"} and re.search(r"->|\b(callback|handler|operator|virtual|cast|dispatch)\b", combined):
        risky, reason = True, f"{family}-family dereference/dispatch pattern"
    if risky:
        hints.append(ToolHint(
            role=role,
            confidence=.60,
            reasons=[reason],
            next_actions=["READ surrounding function and confirm source-backed trigger semantics before recording a sink"],
            family=family or None,
        ))
    return hints


def _family_from_state(state: Any) -> str:
    paths = list(_value(state, "ranked_vulnerability_paths", []) or [])
    if paths:
        family = str(_value(paths[0], "candidate_family", "") or "")
        if family:
            return family
    return str(_value(state, "vulnerability_class", "") or _value(state, "bug_type", "") or "")


def _dedupe_hints(hints: Iterable[ToolHint], limit: int = 4) -> list[ToolHint]:
    selected: list[ToolHint] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for hint in sorted(
        hints,
        key=lambda item: (-_ROLE_WEIGHT.get(item.role, .1), -float(item.confidence or 0.0)),
    ):
        key = (hint.role, hint.path_id, hint.candidate_id)
        if key in seen:
            continue
        seen.add(key)
        hint.confidence = round(max(0.0, min(1.0, float(hint.confidence or 0.0))), 3)
        hint.reasons = [str(item)[:180] for item in hint.reasons[:2] if str(item).strip()]
        hint.next_actions = [str(item)[:220] for item in hint.next_actions[:2] if str(item).strip()]
        selected.append(hint)
        if len(selected) >= limit:
            break
    return selected


def annotate_file_path(state: Any, path: str) -> list[ToolHint]:
    if state is None:
        return []
    hints: list[ToolHint] = []
    hints.extend(_harness_hints(state, path))
    hints.extend(_path_hints(state, path))
    hints.extend(_candidate_hints(state, path))
    hints.extend(_chain_hints(state, path))
    hints.extend(_repo_index_hint(state, path))

    lowered = _clean_path(path).casefold()
    description_hits = sorted(term for term in _description_terms(state) if term in lowered)[:3]
    if description_hits:
        hints.append(ToolHint(
            role=_function_role(lowered, "path_anchor"),
            confidence=.50,
            reasons=[f"description term match: {', '.join(description_hits)}"],
            next_actions=["GREP this file for the described operation, then READ the strongest hit"],
        ))
    hints.extend(_lexical_hint(path, "", _family_from_state(state)))
    return _dedupe_hints(hints)


def annotate_text_hit(state: Any, path: str, line: int, text: str) -> list[ToolHint]:
    if state is None:
        return []
    hints: list[ToolHint] = []
    hints.extend(_path_hints(state, path, line))
    hints.extend(_candidate_hints(state, path, line))
    hints.extend(_chain_hints(state, path, line))
    hints.extend(_repo_index_hint(state, path, line, line))
    hints.extend(_lexical_hint(path, text, _family_from_state(state)))
    return _dedupe_hints(hints)


def annotate_read_region(
    state: Any,
    path: str,
    start: int,
    end: int,
    content: str,
) -> list[ToolHint]:
    if state is None:
        return []
    middle = max(start, (start + end) // 2)
    hints: list[ToolHint] = []
    hints.extend(_path_hints(state, path, middle))
    hints.extend(_candidate_hints(state, path, middle))
    hints.extend(_chain_hints(state, path, middle))
    hints.extend(_harness_hints(state, path))
    hints.extend(_repo_index_hint(state, path, start, end))
    hints.extend(_lexical_hint(path, content, _family_from_state(state)))
    return _dedupe_hints(hints, limit=5)


def rank_annotated_hits(hits: list[AnnotatedHit]) -> list[AnnotatedHit]:
    for hit in hits:
        role_score = max((_ROLE_WEIGHT.get(hint.role, .1) for hint in hit.hints), default=0.0)
        confidence = max((hint.confidence for hint in hit.hints), default=0.0)
        hit.score = round(max(float(hit.score or 0.0), role_score + .20 * confidence), 4)
    return sorted(hits, key=lambda item: (-item.score, _clean_path(item.path), int(item.line or 0)))


def hint_to_dict(hint: ToolHint) -> dict[str, Any]:
    return {key: value for key, value in asdict(hint).items() if value not in (None, "", [], {})}
