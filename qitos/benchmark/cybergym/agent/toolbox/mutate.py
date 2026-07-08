"""Mutation operations for binary PoC files."""

from __future__ import annotations

import struct
from typing import Optional


def patch_bytes(data: bytes, offset: int, patch: bytes) -> bytes:
    """Write `patch` at `offset` in `data`, returning new bytes.

    If offset + len(patch) > len(data), data is extended with zeros.
    """
    result = bytearray(data)
    end = offset + len(patch)
    if end > len(result):
        result.extend(b'\x00' * (end - len(result)))
    result[offset:end] = patch
    return bytes(result)


def patch_int(data: bytes, offset: int, value: int, fmt: str = "<I") -> bytes:
    """Write an integer at `offset` using struct format `fmt`."""
    packed = struct.pack(fmt, value)
    return patch_bytes(data, offset, packed)


def append_bytes(data: bytes, suffix: bytes) -> bytes:
    """Append `suffix` to `data`."""
    return data + suffix


def truncate_bytes(data: bytes, size: int) -> bytes:
    """Truncate `data` to `size` bytes. If size > len(data), pad with zeros."""
    if size <= len(data):
        return data[:size]
    return data + b'\x00' * (size - len(data))


def bit_patch(data: bytes, offset: int, mask: int, value: int) -> bytes:
    """Patch individual bits at `offset` using `mask` and `value`.

    For each bit set in `mask`, the corresponding bit in data[offset] is
    replaced with the bit from `value`.
    """
    result = bytearray(data)
    if offset < len(result):
        original = result[offset]
        result[offset] = (original & ~mask) | (value & mask)
    return bytes(result)
