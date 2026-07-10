"""Private JSON repair helpers shared by parser and runtime code."""

from __future__ import annotations

from typing import Optional


def escape_json_string_control_chars(text: str) -> Optional[str]:
    """Escape bare control characters that appear inside JSON strings."""
    if not isinstance(text, str) or not text:
        return None

    out: list[str] = []
    in_string = False
    escape = False
    changed = False

    for char in text:
        if escape:
            out.append(char)
            escape = False
            continue
        if in_string and char == "\\":
            out.append(char)
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        if in_string and ord(char) < 0x20:
            changed = True
            if char == "\n":
                out.append("\\n")
            elif char == "\r":
                out.append("\\r")
            elif char == "\t":
                out.append("\\t")
            elif char == "\b":
                out.append("\\b")
            elif char == "\f":
                out.append("\\f")
            else:
                out.append(f"\\u{ord(char):04x}")
            continue
        out.append(char)

    if not changed:
        return None
    return "".join(out)
