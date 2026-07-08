"""Machine-readable capability table for the PoC toolbox.

This module is intentionally small and dependency-free so prompts, repair
hints, tests, and offline evaluators can agree on what the toolbox actually
supports.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_FORMAT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "bmp": {
        "aliases": [],
        "module": "toolbox.formats.bmp",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
    "jpeg": {
        "aliases": ["jpg"],
        "module": "toolbox.formats.jpeg",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
    "pdf": {
        "aliases": [],
        "module": "toolbox.formats.pdf",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
    "png": {
        "aliases": [],
        "module": "toolbox.formats.png",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
    "wav": {
        "aliases": [],
        "module": "toolbox.formats.wav",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
    "zip": {
        "aliases": [],
        "module": "toolbox.formats.zipfmt",
        "minimal": True,
        "inspect": True,
        "patch": True,
        "append": True,
        "truncate": True,
    },
}

_ALIAS_TO_FORMAT = {
    alias: fmt
    for fmt, cap in _FORMAT_CAPABILITIES.items()
    for alias in cap.get("aliases", [])
}

_MUTATION_CAPABILITIES: dict[str, bool] = {
    "patch": True,
    "append": True,
    "truncate": True,
}

_BINARY_CAPABILITIES: dict[str, bool] = {
    "hexdump": True,
    "find": True,
    "slice": True,
}


def normalize_format(format_id: str | None) -> str:
    """Return the canonical toolbox format id, or a normalized unsupported id."""
    fmt = (format_id or "").strip().lower()
    if not fmt:
        return ""
    fmt = fmt.replace("_", "-")
    return _ALIAS_TO_FORMAT.get(fmt, fmt)


def supported_formats() -> list[str]:
    """Return canonical format ids supported by the toolbox."""
    return sorted(_FORMAT_CAPABILITIES)


def format_capability(format_id: str | None) -> dict[str, Any] | None:
    """Return a copy of the capability record for a format, if supported."""
    fmt = normalize_format(format_id)
    cap = _FORMAT_CAPABILITIES.get(fmt)
    return deepcopy(cap) if cap is not None else None


def supports(format_id: str | None, command: str) -> bool:
    """Return whether the toolbox supports command for the given format."""
    fmt = normalize_format(format_id)
    cmd = (command or "").strip().lower()
    cap = _FORMAT_CAPABILITIES.get(fmt)
    return bool(cap and cap.get(cmd) is True)


def minimal_command(format_id: str | None, output_placeholder: str = "<output>") -> str:
    """Return the recommended CLI snippet for generating a minimal carrier."""
    fmt = normalize_format(format_id)
    if not supports(fmt, "minimal"):
        return ""
    return f"python3 -m toolbox {fmt} minimal > {output_placeholder}"


def inspect_command(format_id: str | None, file_placeholder: str = "<file>") -> str:
    """Return the recommended CLI snippet for inspecting a carrier."""
    fmt = normalize_format(format_id)
    if not supports(fmt, "inspect"):
        return ""
    return f"python3 -m toolbox {fmt} inspect --file {file_placeholder}"


def capabilities_payload() -> dict[str, Any]:
    """Return a stable JSON-serializable toolbox capability payload."""
    return {
        "schema_version": 1,
        "formats": deepcopy(_FORMAT_CAPABILITIES),
        "mutation": deepcopy(_MUTATION_CAPABILITIES),
        "binary": deepcopy(_BINARY_CAPABILITIES),
    }
