"""Shared parsing helpers for text/xml/json decision parsers."""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from qitos.kit.parser.func_parser import parse_first_action_invocation


def norm(token: str) -> str:
    return re.sub(r"[\s_\-]+", "", token.strip().lower())


def extract_labeled_blocks(text: str) -> Dict[str, str]:
    pattern = re.compile(r"(?im)^\s*([A-Za-z][A-Za-z _-]{0,40})\s*:\s*")
    matches = list(pattern.finditer(text))
    blocks: Dict[str, str] = {}
    if not matches:
        return blocks
    for i, m in enumerate(matches):
        key = norm(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[start:end].strip()
        if key and val:
            blocks.setdefault(key, val)
    return blocks


def first_block_value(blocks: Dict[str, str], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = blocks.get(norm(key))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_action_any(blob: str) -> Optional[Dict[str, Any]]:
    if not isinstance(blob, str):
        return None
    text = blob.strip()
    if not text:
        return None

    parsed = parse_first_action_invocation(f"Action: {text}")
    if parsed is not None:
        return parsed
    parsed = parse_first_action_invocation(text)
    if parsed is not None:
        return parsed

    obj = parse_object_like(text)
    if isinstance(obj, dict):
        name = obj.get("name")
        args = obj.get("args", {})
        if isinstance(name, str):
            if not isinstance(args, dict):
                args = {}
            return {"name": name, "args": args}
    return None


def parse_object_like(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def parse_object_like_detailed(
    text: str,
    *,
    json_mode: str = "direct",
    literal_mode: str = "python_literal",
) -> Tuple[Optional[Any], str]:
    try:
        return json.loads(text), json_mode
    except Exception:
        pass
    try:
        return ast.literal_eval(text), literal_mode
    except Exception:
        return None, ""


def strip_code_fences(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_balanced_object_candidates(text: str) -> List[str]:
    src = str(text or "")
    candidates: List[str] = []
    start = -1
    depth = 0
    quote: str | None = None
    escape = False
    for idx, char in enumerate(src):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if depth > 0 and char in {"'", '"'}:
            quote = char
            continue
        if char == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif char == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start != -1:
                snippet = src[start : idx + 1].strip()
                if snippet:
                    candidates.append(snippet)
                start = -1
    if start != -1:
        snippet = src[start:].strip()
        if snippet:
            candidates.append(snippet)
    unique: List[str] = []
    seen = set()
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def parse_jsonish_object_detailed(
    raw_output: Any,
) -> tuple[Optional[Dict[str, Any]], List[str], str]:
    if isinstance(raw_output, dict):
        return raw_output, [], "direct"
    if not isinstance(raw_output, str):
        return None, ["JSON parser expects dict or JSON string output"], ""
    text = raw_output.strip()
    if not text:
        return None, ["Empty JSON output"], ""

    warnings: List[str] = []
    direct, direct_mode = parse_object_like_detailed(
        text, json_mode="direct", literal_mode="python_literal"
    )
    if isinstance(direct, dict):
        if direct_mode == "python_literal":
            warnings.append(
                "AUTO-CORRECTED: parsed JSON-like payload using Python literal rules."
            )
        return direct, warnings, direct_mode

    stripped = strip_code_fences(text)
    if stripped != text:
        nested, nested_warnings, nested_mode = parse_jsonish_object_detailed(stripped)
        if nested is not None:
            warnings.append(
                "AUTO-CORRECTED: stripped markdown code fences around JSON-like payload."
            )
            return (
                nested,
                warnings + nested_warnings,
                nested_mode if nested_mode != "direct" else "fenced",
            )

    candidates = extract_balanced_object_candidates(text)
    if not candidates:
        return None, warnings + ["No JSON object found in model output."], ""

    candidates = sorted(candidates, key=len, reverse=True)
    for candidate in candidates:
        parsed, parsed_mode = parse_object_like_detailed(
            candidate, json_mode="extracted", literal_mode="python_literal"
        )
        if isinstance(parsed, dict):
            before_idx = text.find(candidate)
            after_idx = before_idx + len(candidate) if before_idx >= 0 else -1
            candidate_warnings: List[str] = []
            if before_idx > 0 and text[:before_idx].strip():
                candidate_warnings.append("Extra text detected before the JSON object.")
            if after_idx >= 0 and text[after_idx:].strip():
                candidate_warnings.append("Extra text detected after the JSON object.")
            if candidate != text.strip():
                candidate_warnings.append(
                    "AUTO-CORRECTED: extracted a JSON-like object from surrounding text."
                )
            if parsed_mode == "python_literal":
                candidate_warnings.append(
                    "AUTO-CORRECTED: parsed JSON-like payload using Python literal rules."
                )
            return parsed, warnings + candidate_warnings, parsed_mode

    return (
        None,
        warnings + ["Could not parse any extracted JSON-like object."],
        "extracted",
    )


def parse_jsonish_object(raw_output: Any) -> tuple[Optional[Dict[str, Any]], List[str]]:
    parsed, warnings, _ = parse_jsonish_object_detailed(raw_output)
    return parsed, warnings


def parse_xml_root(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except Exception:
        wrapped = f"<root>{text}</root>"
        return ET.fromstring(wrapped)


def first_xml_text(root: ET.Element, tags: Sequence[str]) -> Optional[str]:
    target = {norm(t) for t in tags}
    for node in root.iter():
        if node is root:
            continue
        if norm(node.tag) in target:
            content = "".join(node.itertext()).strip()
            if content:
                return content
    return None


def parse_xml_action(
    root: ET.Element, action_tags: Sequence[str]
) -> Optional[Dict[str, Any]]:
    targets = {norm(t) for t in action_tags}
    for node in root.iter():
        if norm(node.tag) not in targets:
            continue
        name_attr = node.attrib.get("name", "").strip()
        if name_attr:
            args: Dict[str, Any] = {}
            for arg in node.findall(".//arg"):
                key = arg.attrib.get("name", "").strip()
                if key:
                    args[key] = "".join(arg.itertext()).strip()
            return {"name": name_attr, "args": args}
        body = "".join(node.itertext()).strip()
        if body:
            parsed = parse_action_any(body)
            if parsed is not None:
                return parsed
    return None


def json_payload(raw_output: Any) -> Dict[str, Any]:
    parsed, warnings = parse_jsonish_object(raw_output)
    if isinstance(parsed, dict):
        return parsed
    detail = "; ".join(warnings) if warnings else "Invalid JSON output"
    raise ValueError(detail)


def json_payload_details(raw_output: Any) -> tuple[Dict[str, Any], List[str], str]:
    parsed, warnings, extraction_mode = parse_jsonish_object_detailed(raw_output)
    if isinstance(parsed, dict):
        return parsed, warnings, extraction_mode
    detail = "; ".join(warnings) if warnings else "Invalid JSON output"
    raise ValueError(detail)


def first_dict_value(payload: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    norm_map: Dict[str, Any] = {norm(str(k)): v for k, v in payload.items()}
    for key in keys:
        value = norm_map.get(norm(str(key)))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_json_actions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions_val = payload.get("actions")
    if isinstance(actions_val, list):
        out: List[Dict[str, Any]] = []
        for item in actions_val:
            if isinstance(item, dict):
                name = item.get("name")
                args = item.get("args", {})
                if isinstance(name, str):
                    if not isinstance(args, dict):
                        args = {}
                    out.append({"name": name, "args": args})
            elif isinstance(item, str):
                parsed = parse_action_any(item)
                if parsed is not None:
                    out.append(parsed)
        if out:
            return out

    action_val = payload.get("action")
    if isinstance(action_val, dict):
        name = action_val.get("name")
        args = action_val.get("args", {})
        if isinstance(name, str):
            if not isinstance(args, dict):
                args = {}
            return [{"name": name, "args": args}]
    if isinstance(action_val, str):
        parsed = parse_action_any(action_val)
        if parsed is not None:
            return [parsed]
    return []
