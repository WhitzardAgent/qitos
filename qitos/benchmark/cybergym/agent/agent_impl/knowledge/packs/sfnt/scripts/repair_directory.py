#!/usr/bin/env python3
"""Repair SFNT offset-table search params and table checksums."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.agent_impl.knowledge.packs.sfnt.validator import (
    _expected_search_params,
    _sfnt_table_checksum,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair SFNT table directory metadata")
    parser.add_argument("--seed", required=True, help="Input SFNT font path")
    parser.add_argument("--output", required=True, help="Output repaired font path")
    parser.add_argument("--search-params", action="store_true", help="Recompute searchRange/entrySelector/rangeShift")
    parser.add_argument("--checksums", action="store_true", help="Recompute table checksums")
    parser.add_argument("--head-adjustment", action="store_true", help="Recompute head.checkSumAdjustment when head table exists")
    args = parser.parse_args()

    seed = Path(args.seed)
    if not seed.is_file():
        print(json.dumps({
            "status": "error",
            "reason": "seed_not_found",
            "seed": str(seed),
        }, sort_keys=True))
        return 1

    data = bytearray(seed.read_bytes())
    applied: list[str] = []
    issues: list[str] = []
    if len(data) < 12:
        print(json.dumps({
            "status": "error",
            "reason": "sfnt_header_too_short",
            "size": len(data),
        }, sort_keys=True))
        return 1

    num_tables = struct.unpack(">H", data[4:6])[0]
    directory_end = 12 + num_tables * 16
    if directory_end > len(data):
        print(json.dumps({
            "status": "error",
            "reason": "table_directory_out_of_bounds",
            "directory_end": directory_end,
            "size": len(data),
        }, sort_keys=True))
        return 1

    if args.search_params:
        search_range, entry_selector, range_shift = _expected_search_params(num_tables)
        struct.pack_into(">HHH", data, 6, search_range, entry_selector, range_shift)
        applied.append("search_params")

    table_entries = _table_entries(data, num_tables)
    if args.head_adjustment:
        for entry in table_entries:
            if entry["tag"] == b"head" and entry["offset"] + 12 <= len(data):
                struct.pack_into(">I", data, entry["offset"] + 8, 0)
                applied.append("head_checkSumAdjustment_zeroed")
                break

    if args.checksums:
        for entry in table_entries:
            offset = entry["offset"]
            length = entry["length"]
            if offset + length > len(data):
                issues.append(f"{entry['tag_text']}_range_out_of_bounds")
                continue
            checksum = _sfnt_table_checksum(entry["tag"], bytes(data[offset:offset + length]))
            struct.pack_into(">I", data, entry["entry_offset"] + 4, checksum)
        applied.append("table_checksums")

    if args.head_adjustment:
        adjustment = (0xB1B0AFBA - _font_checksum(bytes(data))) & 0xFFFFFFFF
        for entry in table_entries:
            if entry["tag"] == b"head" and entry["offset"] + 12 <= len(data):
                struct.pack_into(">I", data, entry["offset"] + 8, adjustment)
                applied.append("head_checkSumAdjustment")
                break

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(json.dumps({
        "status": "success",
        "seed": str(seed),
        "output": str(out),
        "applied_operations": applied,
        "issues": issues,
    }, sort_keys=True))
    return 0


def _table_entries(data: bytearray, num_tables: int) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index in range(num_tables):
        entry_offset = 12 + index * 16
        tag, checksum, offset, length = struct.unpack(">4sIII", data[entry_offset:entry_offset + 16])
        entries.append({
            "entry_offset": entry_offset,
            "tag": tag,
            "tag_text": tag.decode("latin-1", errors="replace"),
            "checksum": checksum,
            "offset": offset,
            "length": length,
        })
    return entries


def _font_checksum(data: bytes) -> int:
    padded = data + b"\x00" * ((4 - len(data) % 4) % 4)
    total = 0
    for offset in range(0, len(padded), 4):
        total = (total + struct.unpack(">I", padded[offset:offset + 4])[0]) & 0xFFFFFFFF
    return total


if __name__ == "__main__":
    raise SystemExit(main())
