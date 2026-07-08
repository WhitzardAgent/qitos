"""Stdlib Image/TIFF mutation helpers shared by pack.build and scripts."""

from __future__ import annotations

import struct
from typing import Any

from ...models import RecipeOperation, RecipePlan


def apply_image_operations(seed: bytes, plan: RecipePlan) -> tuple[bytes, tuple[str, ...], tuple[str, ...]]:
    """Apply supported Image/TIFF operations from a RecipePlan.

    Supported operations are intentionally small and deterministic:
    - mutate_tiff_tag: change one TIFF IFD tag value in-place.
    - append_raw_marker: append a trigger marker without breaking the carrier.
    - wrap_exif_app1: wrap TIFF bytes as a minimal JPEG APP1 Exif payload.
    """
    data = bytes(seed)
    applied: list[str] = []
    blocked: list[str] = []

    for op in plan.operations:
        try:
            if op.kind == "mutate_tiff_tag":
                data = mutate_tiff_tag(
                    data,
                    tag=_as_int(op.ast_transform.get("tag"), 0),
                    value=_as_int(op.ast_transform.get("value"), 0),
                )
                applied.append(op.op_id)
            elif op.kind == "append_raw_marker":
                marker = _marker_bytes(op.ast_transform)
                if marker and marker not in data:
                    data += marker
                applied.append(op.op_id)
            elif op.kind == "wrap_exif_app1":
                data = wrap_exif_app1(data)
                applied.append(op.op_id)
            else:
                blocked.append(op.op_id)
        except (ValueError, struct.error):
            blocked.append(op.op_id)

    return data, tuple(applied), tuple(blocked)


def mutate_tiff_tag(data: bytes, *, tag: int, value: int) -> bytes:
    """Mutate the first matching TIFF IFD tag value in-place."""
    endian = _tiff_endian(data)
    if not endian:
        raise ValueError("not_a_tiff_header")
    ifd_offset = struct.unpack(f"{endian}I", data[4:8])[0]
    if ifd_offset < 8 or ifd_offset + 2 > len(data):
        raise ValueError("ifd_offset_out_of_range")

    out = bytearray(data)
    entry_count = struct.unpack(f"{endian}H", data[ifd_offset:ifd_offset + 2])[0]
    entries_start = ifd_offset + 2
    for index in range(entry_count):
        entry_offset = entries_start + index * 12
        if entry_offset + 12 > len(data):
            raise ValueError("ifd_entry_truncated")
        entry_tag, field_type, count = struct.unpack(
            f"{endian}HHI",
            data[entry_offset:entry_offset + 8],
        )
        if entry_tag != tag:
            continue
        value_offset = entry_offset + 8
        if field_type == 3 and count == 1:
            bounded = max(0, min(int(value), 0xFFFF))
            struct.pack_into(f"{endian}H", out, value_offset, bounded)
            out[value_offset + 2:value_offset + 4] = b"\x00\x00"
        else:
            bounded = max(0, min(int(value), 0xFFFFFFFF))
            struct.pack_into(f"{endian}I", out, value_offset, bounded)
        return bytes(out)

    raise ValueError(f"tag_{tag}_not_found")


def wrap_exif_app1(tiff: bytes) -> bytes:
    """Wrap TIFF bytes in a minimal JPEG APP1 Exif container."""
    if tiff.startswith(b"\xff\xd8") and b"Exif\x00\x00" in tiff[:64]:
        return tiff
    app1_payload = b"Exif\x00\x00" + tiff
    if len(app1_payload) + 2 > 0xFFFF:
        raise ValueError("app1_payload_too_large")
    app1 = b"\xff\xe1" + struct.pack(">H", len(app1_payload) + 2) + app1_payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


def _tiff_endian(data: bytes) -> str:
    if len(data) < 8:
        return ""
    if data[:2] == b"II" and struct.unpack("<H", data[2:4])[0] == 42:
        return "<"
    if data[:2] == b"MM" and struct.unpack(">H", data[2:4])[0] == 42:
        return ">"
    return ""


def _marker_bytes(transform: dict[str, Any]) -> bytes:
    marker = transform.get("marker", "")
    marker_hex = transform.get("marker_hex", "")
    if isinstance(marker, bytes):
        return marker
    if marker_hex:
        return bytes.fromhex(str(marker_hex).replace(" ", ""))
    if marker:
        return str(marker).encode("utf-8")
    return b""


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default
