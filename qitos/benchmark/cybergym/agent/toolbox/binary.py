"""Binary inspection utilities for PoC analysis."""

from __future__ import annotations

import struct
from typing import List


def hexdump(data: bytes, offset: int = 0, length: int = 256) -> str:
    """Return a hex dump of `data[offset:offset+length]`.

    Format: 16 bytes per line with ASCII sidebar.
    """
    chunk = data[offset:offset + length]
    lines: List[str] = []
    for i in range(0, len(chunk), 16):
        line_data = chunk[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in line_data)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in line_data)
        lines.append(f"{offset + i:08x}  {hex_part:<48s}  {ascii_part}")
    return "\n".join(lines)


def find_bytes(data: bytes, pattern: bytes) -> List[int]:
    """Find all offsets where `pattern` occurs in `data`."""
    positions: List[int] = []
    pos = 0
    while True:
        idx = data.find(pattern, pos)
        if idx < 0:
            break
        positions.append(idx)
        pos = idx + 1
    return positions


def slice_bytes(data: bytes, offset: int, length: int) -> bytes:
    """Extract `data[offset:offset+length]`."""
    return data[offset:offset + length]


def int_read(data: bytes, offset: int, fmt: str = "<I") -> int:
    """Read an integer from `data` at `offset` using struct format `fmt`."""
    size = struct.calcsize(fmt)
    return struct.unpack(fmt, data[offset:offset + size])[0]


def int_scan(data: bytes, value: int, fmt: str = "<I") -> List[int]:
    """Scan `data` for all occurrences of `value` encoded with `fmt`."""
    packed = struct.pack(fmt, value)
    return find_bytes(data, packed)
