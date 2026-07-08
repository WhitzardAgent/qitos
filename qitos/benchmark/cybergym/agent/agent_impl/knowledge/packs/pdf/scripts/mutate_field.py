#!/usr/bin/env python3
"""Apply simple PDF byte mutations from a JSON plan and emit a JSON summary."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))


def _load_plan(path: str) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _hex_bytes(value: str) -> bytes:
    cleaned = value.replace("0x", "").replace(",", " ").replace("\\x", " ")
    return bytes(int(part, 16) for part in cleaned.split() if part)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply simple PDF byte mutations")
    parser.add_argument("--seed", required=True, help="Input seed PDF")
    parser.add_argument("--plan", required=True, help="JSON mutation plan")
    parser.add_argument("--output", required=True, help="Output candidate path")
    args = parser.parse_args()

    data = bytearray(Path(args.seed).read_bytes())
    plan = _load_plan(args.plan)
    applied: list[str] = []
    blocked: list[str] = []

    for idx, op in enumerate(list(plan.get("operations", []) or [])):
        op_id = str(op.get("op_id") or f"op_{idx}")
        kind = str(op.get("kind") or "")
        if kind == "patch_bytes":
            offset = int(op.get("offset", -1))
            patch = _hex_bytes(str(op.get("hex", "")))
            if offset < 0 or offset + len(patch) > len(data):
                blocked.append(op_id)
                continue
            data[offset:offset + len(patch)] = patch
            applied.append(op_id)
        elif kind == "append_bytes":
            data.extend(_hex_bytes(str(op.get("hex", ""))))
            applied.append(op_id)
        elif kind == "replace_ascii":
            old = str(op.get("old", "")).encode("latin-1")
            new = str(op.get("new", "")).encode("latin-1")
            pos = bytes(data).find(old)
            if not old or pos < 0:
                blocked.append(op_id)
                continue
            data[pos:pos + len(old)] = new
            applied.append(op_id)
        elif kind == "mutate_stream_length":
            result = _mutate_stream_length(data, op)
            if result:
                applied.append(op_id)
            else:
                blocked.append(op_id)
        else:
            blocked.append(op_id)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    print(json.dumps({
        "status": "success" if not blocked else "partial",
        "format": "pdf",
        "output": str(out),
        "applied_operations": applied,
        "blocked_operations": blocked,
        "size": len(data),
    }, sort_keys=True))
    return 0


def _mutate_stream_length(data: bytearray, op: dict) -> bool:
    value = op.get("value")
    if value is None:
        return False
    new_value = str(int(value)).encode("ascii")
    stream_index = int(op.get("stream_index", 0))
    object_number = op.get("object")
    preserve_width = bool(op.get("preserve_width", True))

    candidates = list(re.finditer(rb"(?P<prefix>(?:(?P<object>\d+)\s+\d+\s+obj).*?)<<(?P<dict>.*?)>>\s*stream\r?\n", bytes(data), flags=re.DOTALL))
    if not candidates:
        candidates = list(re.finditer(rb"(?P<prefix>)<<(?P<dict>.*?)>>\s*stream\r?\n", bytes(data), flags=re.DOTALL))

    seen_stream = 0
    for match in candidates:
        if object_number is not None:
            obj = match.groupdict().get("object")
            if not obj or int(obj) != int(object_number):
                continue
        elif seen_stream != stream_index:
            seen_stream += 1
            continue

        dict_start = match.start("dict")
        dict_bytes = match.group("dict")
        length_match = re.search(rb"/Length\s+(?P<length>\d+)", dict_bytes)
        if not length_match:
            return False
        absolute_start = dict_start + length_match.start("length")
        absolute_end = dict_start + length_match.end("length")
        old_width = absolute_end - absolute_start
        if preserve_width and len(new_value) > old_width:
            return False
        replacement = new_value.rjust(old_width, b"0") if preserve_width else new_value
        data[absolute_start:absolute_end] = replacement
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
