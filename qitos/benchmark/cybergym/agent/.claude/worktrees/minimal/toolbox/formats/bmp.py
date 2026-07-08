"""BMP format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict

from ..common import read_u32le, read_u16le


def minimal() -> bytes:
    """Generate a minimal valid 1x1 24-bit BMP."""
    # BMP header (14 bytes) + DIB header (40 bytes) + pixel data (4 bytes, padded)
    pixel_data = b'\xff\x00\x00\x00'  # BGR + padding (1 pixel, 4-byte row alignment)

    file_size = 14 + 40 + len(pixel_data)
    bmp_header = struct.pack(
        "<2sIHHI",
        b'BM',        # signature
        file_size,    # file size
        0,            # reserved
        0,            # reserved
        54,           # pixel data offset
    )
    dib_header = struct.pack(
        "<iiiHHiiiiii",
        40,          # DIB header size
        1,           # width
        1,           # height
        1,           # color planes
        24,          # bits per pixel
        0,           # compression (none)
        len(pixel_data),  # image size
        2835,        # horizontal resolution (72 DPI)
        2835,        # vertical resolution
        0,           # colors in palette
        0,           # important colors
    )
    return bmp_header + dib_header + pixel_data


def inspect(path: str) -> Dict[str, Any]:
    """Parse BMP file structure, returning header details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "bmp",
        "size": len(data),
        "valid_signature": data[:2] == b'BM',
    }

    if not result["valid_signature"]:
        result["error"] = "Invalid BMP signature"
        return result

    if len(data) < 54:
        result["error"] = "BMP header too short"
        return result

    file_size = read_u32le(data, 2)
    pixel_offset = read_u32le(data, 10)
    dib_size = read_u32le(data, 14)
    width = struct.unpack("<i", data[18:22])[0]
    height = struct.unpack("<i", data[22:26])[0]
    planes = read_u16le(data, 26)
    bpp = read_u16le(data, 28)
    compression = read_u32le(data, 30)
    image_size = read_u32le(data, 34)

    result["file_size_declared"] = file_size
    result["pixel_data_offset"] = pixel_offset
    result["dib_header_size"] = dib_size
    result["width"] = width
    result["height"] = height
    result["bits_per_pixel"] = bpp
    result["compression"] = compression
    result["image_size"] = image_size
    result["size_mismatch"] = file_size != len(data)

    return result
