"""JPEG format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List


def minimal() -> bytes:
    """Generate a minimal valid JPEG (SOI + smallest valid frame + EOI).

    This produces a 1x1 grayscale JPEG that most parsers will accept.
    """
    # SOI (Start of Image)
    soi = b'\xff\xd8'

    # JFIF APP0 marker
    app0_data = b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    app0 = b'\xff\xe0' + struct.pack(">H", len(app0_data) + 2) + app0_data

    # DQT (Define Quantization Table) - minimal table
    dqt_data = bytes([0]) + bytes([1] * 64)  # table 0, all-1 values
    dqt = b'\xff\xdb' + struct.pack(">H", len(dqt_data) + 2) + dqt_data

    # SOF0 (Start of Frame) - 1x1 grayscale
    sof_data = bytes([8, 0, 1, 0, 1, 1, 0x11, 0])  # precision, height, width, components...
    sof = b'\xff\xc0' + struct.pack(">H", len(sof_data) + 2) + sof_data

    # DHT (Define Huffman Table) - minimal DC table
    dht_data = bytes([0, 0, 1, 0]) + bytes([0] * 12) + bytes([0] * 162)
    dht = b'\xff\xc4' + struct.pack(">H", len(dht_data) + 2) + dht_data

    # SOS (Start of Scan) - minimal
    sos_data = bytes([1, 0, 0])
    sos = b'\xff\xda' + struct.pack(">H", len(sos_data) + 2) + sos_data

    # Encoded data (single zero DC coefficient)
    scan_data = b'\x00'

    # EOI (End of Image)
    eoi = b'\xff\xd9'

    return soi + app0 + dqt + sof + dht + sos + scan_data + eoi


def inspect(path: str) -> Dict[str, Any]:
    """Parse JPEG file structure, returning marker details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "jpeg",
        "size": len(data),
        "valid_signature": data[:2] == b'\xff\xd8',
    }

    if not result["valid_signature"]:
        result["error"] = "Invalid JPEG signature"
        return result

    markers: List[Dict[str, Any]] = []
    pos = 2  # Skip SOI
    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xD9:  # EOI
            markers.append({"marker": "FFD9", "name": "EOI", "offset": pos})
            break
        if marker == 0xDA:  # SOS - followed by entropy-coded data
            if pos + 3 < len(data):
                seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
                markers.append({
                    "marker": f"FF{marker:02X}",
                    "name": "SOS",
                    "offset": pos,
                    "header_length": seg_len,
                })
            break
        if 0xD0 <= marker <= 0xD7:  # RST markers (no length)
            markers.append({
                "marker": f"FF{marker:02X}",
                "name": f"RST{marker - 0xD0}",
                "offset": pos,
            })
            pos += 2
            continue
        if pos + 3 < len(data):
            seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
            marker_names = {
                0xE0: "APP0", 0xE1: "APP1", 0xE2: "APP2",
                0xC0: "SOF0", 0xC2: "SOF2",
                0xC4: "DHT", 0xDB: "DQT", 0xDD: "DRI",
            }
            name = marker_names.get(marker, f"0x{marker:02X}")
            entry: Dict[str, Any] = {
                "marker": f"FF{marker:02X}",
                "name": name,
                "offset": pos,
                "length": seg_len,
            }
            # Parse SOF0 for dimensions
            if marker == 0xC0 and pos + 9 < len(data):
                precision = data[pos + 4]
                height = struct.unpack(">H", data[pos + 5:pos + 7])[0]
                width = struct.unpack(">H", data[pos + 7:pos + 9])[0]
                entry["precision"] = precision
                entry["height"] = height
                entry["width"] = width
            markers.append(entry)
            pos += 2 + seg_len
        else:
            break

    result["marker_count"] = len(markers)
    result["markers"] = markers
    return result
