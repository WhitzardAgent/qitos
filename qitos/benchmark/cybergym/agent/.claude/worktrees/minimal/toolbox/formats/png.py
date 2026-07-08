"""PNG format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List

from ..common import make_chunk, parse_chunks, read_u32be


def minimal() -> bytes:
    """Generate a minimal valid 1x1 RGBA PNG."""
    signature = b'\x89PNG\r\n\x1a\n'

    # IHDR: 1x1, 8-bit RGBA
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr = make_chunk(b'IHDR', ihdr_data)

    # IDAT: single scanline (filter byte 0 + 4 RGBA bytes), zlib compressed
    import zlib
    raw_scanline = b'\x00\xff\x00\x00\xff'  # filter=0, R=255, G=0, B=0, A=255
    compressed = zlib.compress(raw_scanline)
    idat = make_chunk(b'IDAT', compressed)

    # IEND
    iend = make_chunk(b'IEND', b'')

    return signature + ihdr + idat + iend


def inspect(path: str) -> Dict[str, Any]:
    """Parse PNG file structure, returning chunk details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "png",
        "size": len(data),
        "valid_signature": data[:8] == b'\x89PNG\r\n\x1a\n',
    }

    if not result["valid_signature"]:
        result["error"] = "Invalid PNG signature"
        return result

    chunks = parse_chunks(data, start=8)
    result["chunk_count"] = len(chunks)
    result["chunks"] = []

    for ch in chunks:
        entry: Dict[str, Any] = {
            "type": ch["type"],
            "offset": ch["offset"],
            "length": ch["length"],
            "crc_valid": ch["crc_valid"],
        }
        if ch["truncated"]:
            entry["truncated"] = True

        # Parse IHDR
        if ch["type"] == "IHDR" and ch["length"] == 13:
            chunk_data = data[ch["data_offset"]:ch["data_offset"] + 13]
            width = struct.unpack(">I", chunk_data[0:4])[0]
            height = struct.unpack(">I", chunk_data[4:8])[0]
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            color_names = {0: "grayscale", 2: "RGB", 3: "indexed", 4: "grayscale+alpha", 6: "RGBA"}
            entry["width"] = width
            entry["height"] = height
            entry["bit_depth"] = bit_depth
            entry["color_type"] = color_names.get(color_type, f"unknown({color_type})")

        result["chunks"].append(entry)

    return result
