"""Format-aware toolbox for CyberGym PoC generation.

Provides minimal carrier generation, structure inspection, and mutation
operations for common binary formats (PNG, JPEG, ZIP, PDF, BMP, WAV).

Usage via CLI:
    python3 -m toolbox <domain> <command> [options]
    python3 -m toolbox png minimal
    python3 -m toolbox png inspect --file poc.bin
    python3 -m toolbox mutate patch --offset 4 --hex 00FF
"""

from .formats import png, jpeg, zipfmt, pdf, bmp, wav
from .mutate import patch_bytes, append_bytes, truncate_bytes
from .binary import hexdump, find_bytes, slice_bytes
from .capabilities import (
    capabilities_payload,
    format_capability,
    inspect_command,
    minimal_command,
    normalize_format,
    supported_formats,
    supports,
)

__all__ = [
    "png", "jpeg", "zipfmt", "pdf", "bmp", "wav",
    "patch_bytes", "append_bytes", "truncate_bytes",
    "hexdump", "find_bytes", "slice_bytes",
    "capabilities_payload", "format_capability", "inspect_command",
    "minimal_command", "normalize_format", "supported_formats", "supports",
]
