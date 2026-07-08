"""WAV format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List

from ..common import read_u16le, read_u32le


def minimal() -> bytes:
    """Generate a minimal valid WAV (1 sample, 8-bit mono, 8000 Hz)."""
    # RIFF header
    num_samples = 1
    sample_rate = 8000
    bits_per_sample = 8
    num_channels = 1
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align
    fmt_size = 16
    riff_size = 4 + (8 + fmt_size) + (8 + data_size)

    riff = struct.pack(
        "<4sI4s",
        b'RIFF',
        riff_size,
        b'WAVE',
    )
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b'fmt ',
        fmt_size,
        1,              # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    data_chunk = struct.pack(
        "<4sI",
        b'data',
        data_size,
    )
    sample_data = b'\x80'  # Middle value for 8-bit PCM

    return riff + fmt_chunk + data_chunk + sample_data


def inspect(path: str) -> Dict[str, Any]:
    """Parse WAV file structure, returning chunk and format details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "wav",
        "size": len(data),
        "valid_signature": data[:4] == b'RIFF' and data[8:12] == b'WAVE',
    }

    if not result["valid_signature"]:
        result["error"] = "Invalid WAV signature"
        return result

    # Parse RIFF header
    riff_size = read_u32le(data, 4)
    result["riff_size"] = riff_size
    result["size_mismatch"] = riff_size + 8 != len(data)

    # Parse sub-chunks
    chunks: List[Dict[str, Any]] = []
    pos = 12  # After RIFF header
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4].decode("latin-1", errors="replace")
        chunk_size = read_u32le(data, pos + 4)
        entry: Dict[str, Any] = {
            "id": chunk_id,
            "offset": pos,
            "size": chunk_size,
        }

        # Parse fmt chunk
        if chunk_id == "fmt " and pos + 24 <= len(data):
            audio_format = read_u16le(data, pos + 8)
            channels = read_u16le(data, pos + 10)
            sample_rate = read_u32le(data, pos + 12)
            byte_rate = read_u32le(data, pos + 16)
            block_align = read_u16le(data, pos + 20)
            bits_per_sample = read_u16le(data, pos + 22)
            format_names = {1: "PCM", 3: "IEEE_FLOAT", 6: "A-LAW", 7: "MU-LAW"}
            entry["audio_format"] = format_names.get(audio_format, f"unknown({audio_format})")
            entry["channels"] = channels
            entry["sample_rate"] = sample_rate
            entry["byte_rate"] = byte_rate
            entry["block_align"] = block_align
            entry["bits_per_sample"] = bits_per_sample

        chunks.append(entry)
        pos += 8 + chunk_size
        # WAV chunks are word-aligned
        if chunk_size % 2 != 0:
            pos += 1

    result["chunk_count"] = len(chunks)
    result["chunks"] = chunks
    return result
