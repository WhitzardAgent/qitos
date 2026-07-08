"""ZIP format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List

from ..common import read_u16le, read_u32le


def minimal() -> bytes:
    """Generate a minimal valid ZIP with one empty file."""
    # Local file header
    local_header = struct.pack(
        "<IHHHHHIIIHH",
        0x04034b50,  # signature
        20,          # version needed
        0,          # flags
        0,          # compression (stored)
        0,          # mod time
        0,          # mod date
        0,          # crc32
        0,          # compressed size
        0,          # uncompressed size
        1,          # filename length
        0,          # extra field length
    ) + b"a"  # filename

    # Central directory
    central_header = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014b50,  # signature
        20,          # version made by
        20,          # version needed
        0,          # flags
        0,          # compression
        0,          # mod time
        0,          # mod date
        0,          # crc32
        0,          # compressed size
        0,          # uncompressed size
        1,          # filename length
        0,          # extra field length
        0,          # file comment length
        0,          # disk number start
        0,          # internal attributes
        0,          # external attributes
        0,          # local header offset
    ) + b"a"

    # End of central directory
    local_header_size = len(local_header)
    central_size = len(central_header)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054b50,  # signature
        0,          # disk number
        0,          # disk with central dir
        1,          # entries on disk
        1,          # total entries
        central_size,  # central dir size
        local_header_size,  # central dir offset
        0,          # comment length
    )

    return local_header + central_header + eocd


def inspect(path: str) -> Dict[str, Any]:
    """Parse ZIP file structure, returning entry details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "zip",
        "size": len(data),
        "valid_signature": data[:4] == b'PK\x03\x04',
    }

    if not result["valid_signature"]:
        # Check for end-of-central-dir signature
        if b'PK\x05\x06' in data:
            result["valid_signature"] = True
            result["note"] = "EOCD found but no local file header at start"
        else:
            result["error"] = "Invalid ZIP signature"
            return result

    entries: List[Dict[str, Any]] = []
    pos = 0

    # Parse local file headers
    while pos + 30 <= len(data):
        sig = data[pos:pos + 4]
        if sig != b'PK\x03\x04':
            break
        flags = read_u16le(data, pos + 6)
        compression = read_u16le(data, pos + 8)
        crc = read_u32le(data, pos + 14)
        comp_size = read_u32le(data, pos + 18)
        uncomp_size = read_u32le(data, pos + 22)
        name_len = read_u16le(data, pos + 26)
        extra_len = read_u16le(data, pos + 28)
        name = data[pos + 30:pos + 30 + name_len].decode("utf-8", errors="replace")
        entry: Dict[str, Any] = {
            "type": "local_file",
            "offset": pos,
            "filename": name,
            "compression": compression,
            "compressed_size": comp_size,
            "uncompressed_size": uncomp_size,
            "crc32": f"{crc:08x}",
            "flags": flags,
        }
        if flags & 0x1:
            entry["encrypted"] = True
        entries.append(entry)
        pos += 30 + name_len + extra_len + comp_size

    result["entry_count"] = len(entries)
    result["entries"] = entries
    return result
