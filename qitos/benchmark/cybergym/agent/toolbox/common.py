"""Shared utilities for the format-aware toolbox."""

from __future__ import annotations

import struct
from typing import Any, Dict, List


def crc32(data: bytes) -> int:
    """Compute CRC-32 as used by PNG/ZIP."""
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a PNG-style chunk: length + type + data + CRC."""
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", crc32(chunk_type + data))
    return length + chunk_type + data + crc


def parse_chunks(data: bytes, start: int = 8) -> List[Dict[str, Any]]:
    """Parse PNG-style chunks from raw data starting at `start` offset.

    Returns list of dicts with: type, offset, length, data, crc_valid.
    """
    chunks: List[Dict[str, Any]] = []
    pos = start
    while pos + 12 <= len(data):
        chunk_len = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8].decode("latin-1", errors="replace")
        if pos + 12 + chunk_len > len(data):
            # Truncated chunk
            chunks.append({
                "type": chunk_type,
                "offset": pos,
                "length": chunk_len,
                "data_offset": pos + 8,
                "data_length": len(data) - pos - 12,
                "crc_valid": False,
                "truncated": True,
            })
            break
        chunk_data = data[pos + 8:pos + 8 + chunk_len]
        stored_crc = struct.unpack(">I", data[pos + 8 + chunk_len:pos + 12 + chunk_len])[0]
        actual_crc = crc32(data[pos + 4:pos + 8 + chunk_len])
        chunks.append({
            "type": chunk_type,
            "offset": pos,
            "length": chunk_len,
            "data_offset": pos + 8,
            "data_length": chunk_len,
            "crc_valid": stored_crc == actual_crc,
            "truncated": False,
        })
        pos += 12 + chunk_len
    return chunks


def read_u16be(data: bytes, offset: int) -> int:
    return struct.unpack(">H", data[offset:offset + 2])[0]


def read_u32be(data: bytes, offset: int) -> int:
    return struct.unpack(">I", data[offset:offset + 4])[0]


def read_u16le(data: bytes, offset: int) -> int:
    return struct.unpack("<H", data[offset:offset + 2])[0]


def read_u32le(data: bytes, offset: int) -> int:
    return struct.unpack("<I", data[offset:offset + 4])[0]
