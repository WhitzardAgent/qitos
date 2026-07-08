"""Structured rewriter — applies conservative local rewrites to a seed file.

This is NOT a general-purpose binary editor.  It only supports a small set
of controlled operations that preserve carrier structure while mutating
specific fields to trigger vulnerabilities.

Operations:
- replace_bytes(offset, data)
- set_u16/u32/u64(offset, value, endian)
- insert_bytes(offset, data)
- truncate(length)
- duplicate_range(src_offset, length, dst_offset)
- recompute_length(field_offset, target_range)
- recompute_checksum(kind, offset, range)

The rewriter does NOT execute arbitrary Python.  It is a safety-auditable
layer between the agent's knowledge and the actual PoC bytes.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any


def apply_structured_rewrite(
    *,
    seed_path: str,
    out_path: str,
    rewrite_plan: dict[str, Any] | Any,
    mutations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply conservative local rewrites and return a structured result.

    Accepts both the legacy dict rewrite_plan and the new RecipePlan dataclass.
    When a RecipePlan is provided, operations are applied in DAG dependency
    order and derived fields trigger backpatch recomputation.

    Returns a dict with:
      status: "success" | "partial" | "blocked"
      out_path: path to the output file
      applied_operations: list of operations that were applied
      blocked_reason: reason if status is "blocked"
      sanity_expectations: from the rewrite plan
    """
    result: dict[str, Any] = {
        "status": "success",
        "out_path": out_path,
        "applied_operations": [],
        "blocked_reason": "",
        "sanity_expectations": [],
    }

    # Read seed
    seed = Path(seed_path)
    if not seed.is_file():
        result["status"] = "blocked"
        result["blocked_reason"] = f"Seed file not found: {seed_path}"
        return result

    data = bytearray(seed.read_bytes())

    # Handle RecipePlan dataclass input
    from ..knowledge.models import RecipePlan as RecipePlanDataclass
    from ..knowledge.recipe_ir import topological_sort_ops, apply_backpatch, recipe_to_dict

    if isinstance(rewrite_plan, RecipePlanDataclass):
        # Sort operations by DAG dependency order
        sorted_ops = topological_sort_ops(rewrite_plan.operations)

        # Convert RecipePlan operations to legacy dict format
        operations = []
        for op in sorted_ops:
            op_dict: dict[str, Any] = {
                "kind": op.kind,
                "op_id": op.op_id,
                "target_node_id": op.target_node_id,
            }
            # Convert ast_transform to concrete operations
            transform = op.ast_transform if hasattr(op, "ast_transform") else {}
            if transform:
                op_dict.update(transform)
            operations.append(op_dict)

        invariants = [
            {"invariant_id": inv.invariant_id, "kind": inv.kind,
             "expression": inv.expression, "protected": inv.protected}
            for inv in rewrite_plan.invariants
        ]
    else:
        operations = list(rewrite_plan.get("operations", []) or [])
        invariants = list(rewrite_plan.get("invariants", []) or [])

    # Apply mutations first (from recipe trigger_mutations)
    for mut in mutations:
        if not isinstance(mut, dict):
            continue
        if not mut.get("executable"):
            continue

        offset = mut.get("offset")
        width = mut.get("width")
        strategy = str(mut.get("value_strategy", ""))
        endian = str(mut.get("endian", "big"))

        if offset is None or width is None:
            continue

        offset = int(offset)
        width = int(width)

        if offset < 0 or offset + width > len(data):
            result["status"] = "partial"
            continue

        # Apply explicit solver assignment first, then strategy-based value.
        value = mut.get("value")
        if value is None:
            value = _strategy_value(strategy, width)
        if value is not None:
            value = int(value)
            _write_integer(data, offset, value, width, endian=endian)
            result["applied_operations"].append({
                "kind": "set_value",
                "offset": offset,
                "width": width,
                "value": value,
                "strategy": strategy,
                "endian": endian,
            })

    # Apply rewrite plan operations
    for op in operations:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("kind", "")).lower()
        applied = _apply_operation(data, kind, op)
        if applied:
            result["applied_operations"].append(applied)
        elif kind.startswith("recompute"):
            # Recompute operations need special handling
            recomputed = _apply_recompute(data, kind, op)
            if recomputed:
                result["applied_operations"].append(recomputed)

    # Write output
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(bytes(data))
    except OSError as e:
        result["status"] = "blocked"
        result["blocked_reason"] = f"Failed to write output: {e}"
        return result

    # Backpatch derived fields if using RecipePlan
    if isinstance(rewrite_plan, RecipePlanDataclass):
        recompute_ops = apply_backpatch(
            rewrite_plan.operations,
            results_so_far={"field_map": {}},
        )
        for rc_op in recompute_ops[:3]:
            # Apply recompute as a rewrite operation
            rc_dict = {"kind": rc_op.kind, "op_id": rc_op.op_id,
                       "target_node_id": rc_op.target_node_id}
            applied_rc = _apply_recompute(data, rc_op.kind, rc_dict)
            if applied_rc:
                result["applied_operations"].append(applied_rc)

    # Set sanity expectations from invariants
    result["sanity_expectations"] = [
        {"kind": "invariant", "description": inv}
        for inv in invariants[:4]
    ]

    return result


def _strategy_value(strategy: str, width: int) -> int | None:
    """Map a value_strategy to an integer value of the given width."""
    max_val = (1 << (width * 8)) - 1

    if strategy == "oversize":
        # Set to maximum value for the width
        return max_val
    elif strategy == "negative":
        # Set a high value that would be negative when interpreted as signed
        if width == 4:
            return 0xFFFFFFFF
        elif width == 2:
            return 0xFFFF
        elif width == 8:
            return 0xFFFFFFFFFFFFFFFF
        return max_val
    elif strategy == "wrap":
        # Near-overflow value
        return max_val
    elif strategy == "choose_case":
        # Choose a non-default case — use 1 as a reasonable selector
        return 1
    elif strategy == "null_or_stale":
        # Zero/null pointer
        return 0
    elif strategy == "duplicate_free_sequence":
        # Value that triggers state transition — use 2
        return 2

    return None


def _write_integer(data: bytearray, offset: int, value: int, width: int, endian: str = "big") -> None:
    """Write an integer value at the given offset with specified endianness."""
    fmt_prefix = ">" if endian == "big" else "<"
    if width == 1:
        data[offset] = value & 0xFF
    elif width == 2:
        struct.pack_into(f"{fmt_prefix}H", data, offset, value & 0xFFFF)
    elif width == 4:
        struct.pack_into(f"{fmt_prefix}I", data, offset, value & 0xFFFFFFFF)
    elif width == 8:
        struct.pack_into(f"{fmt_prefix}Q", data, offset, value & 0xFFFFFFFFFFFFFFFF)


def _apply_operation(data: bytearray, kind: str, op: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a single rewrite operation. Returns applied op dict or None."""
    try:
        if kind == "replace_bytes":
            offset = int(op.get("offset", -1))
            replace_data = op.get("data", b"")
            if isinstance(replace_data, str):
                replace_data = bytes.fromhex(replace_data)
            if offset < 0 or offset + len(replace_data) > len(data):
                return None
            data[offset:offset + len(replace_data)] = replace_data
            return {"kind": "replace_bytes", "offset": offset, "length": len(replace_data)}

        elif kind == "set_u8":
            offset = int(op.get("offset", -1))
            value = int(op.get("value", 0))
            if offset < 0 or offset + 1 > len(data):
                return None
            data[offset] = value & 0xFF
            return {"kind": "set_u8", "offset": offset, "value": value}

        elif kind == "set_u16":
            offset = int(op.get("offset", -1))
            value = int(op.get("value", 0))
            endian = ">" if op.get("endian", "big") == "big" else "<"
            if offset < 0 or offset + 2 > len(data):
                return None
            struct.pack_into(f"{endian}H", data, offset, value & 0xFFFF)
            return {"kind": "set_u16", "offset": offset, "value": value}

        elif kind == "set_u32":
            offset = int(op.get("offset", -1))
            value = int(op.get("value", 0))
            endian = ">" if op.get("endian", "big") == "big" else "<"
            if offset < 0 or offset + 4 > len(data):
                return None
            struct.pack_into(f"{endian}I", data, offset, value & 0xFFFFFFFF)
            return {"kind": "set_u32", "offset": offset, "value": value}

        elif kind == "set_u64":
            offset = int(op.get("offset", -1))
            value = int(op.get("value", 0))
            endian = ">" if op.get("endian", "big") == "big" else "<"
            if offset < 0 or offset + 8 > len(data):
                return None
            struct.pack_into(f"{endian}Q", data, offset, value & 0xFFFFFFFFFFFFFFFF)
            return {"kind": "set_u64", "offset": offset, "value": value}

        elif kind == "insert_bytes":
            offset = int(op.get("offset", -1))
            insert_data = op.get("data", b"")
            if isinstance(insert_data, str):
                insert_data = bytes.fromhex(insert_data)
            if offset < 0 or offset > len(data):
                return None
            data[offset:offset] = insert_data
            return {"kind": "insert_bytes", "offset": offset, "length": len(insert_data)}

        elif kind == "truncate":
            length = int(op.get("length", -1))
            if length < 0 or length > len(data):
                return None
            del data[length:]
            return {"kind": "truncate", "length": length}

        elif kind == "duplicate_range":
            src_offset = int(op.get("src_offset", -1))
            length = int(op.get("length", 0))
            dst_offset = int(op.get("dst_offset", -1))
            if src_offset < 0 or src_offset + length > len(data):
                return None
            if dst_offset < 0:
                return None
            chunk = bytes(data[src_offset:src_offset + length])
            data[dst_offset:dst_offset] = chunk
            return {"kind": "duplicate_range", "src": src_offset, "dst": dst_offset, "length": length}

        elif kind == "pad_to_length":
            length = int(op.get("length", -1))
            pad_byte = int(op.get("pad_byte", 0)) & 0xFF
            if length < 0 or length <= len(data):
                return None
            data.extend(bytes([pad_byte]) * (length - len(data)))
            return {"kind": "pad_to_length", "length": length}

        elif kind == "set_bytes_ascii":
            offset = int(op.get("offset", -1))
            text = str(op.get("text", ""))
            raw = text.encode("latin1", errors="ignore")
            if offset < 0:
                return None
            if offset + len(raw) > len(data):
                data.extend(b"\0" * (offset + len(raw) - len(data)))
            data[offset:offset + len(raw)] = raw
            return {"kind": "set_bytes_ascii", "offset": offset, "length": len(raw)}

    except (struct.error, ValueError, IndexError):
        return None

    return None


def _apply_recompute(data: bytearray, kind: str, op: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a recompute operation (length or checksum)."""
    try:
        if kind in {"recompute_length", "backpatch_length", "backpatch_offset"}:
            field_offset = int(op.get("field_offset", -1))
            if "offset" in op and field_offset < 0:
                field_offset = int(op.get("offset", -1))
            width = int(op.get("width", 4))
            target_start = int(op.get("target_start", 0))
            target_end = int(op.get("target_end", len(data)))
            if field_offset < 0 or field_offset + width > len(data):
                return None
            length = int(op.get("value", target_end - target_start))
            endian = ">" if op.get("endian", "big") == "big" else "<"
            if width == 1:
                data[field_offset] = length & 0xFF
            if width == 2:
                struct.pack_into(f"{endian}H", data, field_offset, length & 0xFFFF)
            elif width == 4:
                struct.pack_into(f"{endian}I", data, field_offset, length & 0xFFFFFFFF)
            elif width == 8:
                struct.pack_into(f"{endian}Q", data, field_offset, length & 0xFFFFFFFFFFFFFFFF)
            return {"kind": kind, "field_offset": field_offset, "value": length}

        elif kind == "recompute_checksum":
            checksum_kind = str(op.get("checksum_kind", "sum32")).lower()
            field_offset = int(op.get("field_offset", -1))
            range_start = int(op.get("range_start", 0))
            range_end = int(op.get("range_end", len(data)))
            if field_offset < 0 or field_offset + 4 > len(data):
                return None
            if range_end > len(data):
                range_end = len(data)

            chunk = data[range_start:range_end]
            if checksum_kind == "sum32":
                total = 0
                padded = bytes(chunk) + b"\0" * ((4 - len(chunk) % 4) % 4)
                for i in range(0, len(padded), 4):
                    total = (total + struct.unpack(">I", padded[i:i + 4])[0]) & 0xFFFFFFFF
                struct.pack_into(">I", data, field_offset, total)
                return {"kind": "recompute_checksum", "checksum_kind": "sum32", "value": total}
            elif checksum_kind == "crc32":
                import zlib
                crc = zlib.crc32(bytes(chunk)) & 0xFFFFFFFF
                endian = ">" if op.get("endian", "big") == "big" else "<"
                struct.pack_into(f"{endian}I", data, field_offset, crc)
                return {"kind": "recompute_checksum", "checksum_kind": "crc32", "value": crc}
            elif checksum_kind == "adler32":
                import zlib
                adler = zlib.adler32(bytes(chunk)) & 0xFFFFFFFF
                endian = ">" if op.get("endian", "big") == "big" else "<"
                struct.pack_into(f"{endian}I", data, field_offset, adler)
                return {"kind": "recompute_checksum", "checksum_kind": "adler32", "value": adler}
            elif checksum_kind == "sum16":
                total = 0
                for b in chunk:
                    total = (total + b) & 0xFFFF
                endian = ">" if op.get("endian", "big") == "big" else "<"
                struct.pack_into(f"{endian}H", data, field_offset, total)
                return {"kind": "recompute_checksum", "checksum_kind": "sum16", "value": total}

    except (struct.error, ValueError, IndexError, ImportError):
        return None

    return None
