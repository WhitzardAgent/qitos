"""API reachability analysis — determines which harness API paths,
table selectors, and architecture modes are reachable from input.

Used for cases like Harfbuzz font table reachability, Capstone arch
selector, and Wireshark dissector dispatch.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def analyze_api_reachability(
    *,
    harness_files: list[str],
    source_root: str = "",
    harness_protocols: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyze which API paths in the harness are reachable from input.

    Returns:
      harness_apis: list of reachable API paths with selector conditions
      table_selectors: font/dissector table selector mappings
      arch_selectors: architecture/opcode selector mappings
      mode_selectors: mode/state machine selector mappings
    """
    result: dict[str, Any] = {
        "harness_apis": [],
        "table_selectors": [],
        "arch_selectors": [],
        "mode_selectors": [],
    }

    all_text = ""
    for hf in harness_files:
        hpath = Path(source_root) / hf if source_root else Path(hf)
        if not hpath.is_file():
            continue
        try:
            text = hpath.read_text(errors="replace")
        except OSError:
            continue
        all_text += text + "\n"

    if not all_text:
        return result

    # Protocol hints
    protocols = harness_protocols or []

    # Detect switch/dispatch on selector
    _detect_switch_dispatch(all_text, result)
    _detect_font_table_dispatch(all_text, result)
    _detect_architecture_dispatch(all_text, result)
    _detect_mode_dispatch(all_text, result)

    return result


def _detect_switch_dispatch(text: str, result: dict[str, Any]) -> None:
    """Detect switch statements that dispatch on input selectors."""
    # Pattern: switch(expr) { case VALUE: ... }
    # Use a more permissive match and then extract cases/calls
    switch_matches = list(re.finditer(
        r'switch\s*\(\s*([^)]+)\s*\)\s*\{',
        text,
    ))
    for smatch in switch_matches:
        selector_expr = smatch.group(1).strip()
        # Extract the selector variable (simplify expression)
        selector_var = re.match(r'(\w+)', selector_expr)
        if not selector_var:
            continue
        selector_var = selector_var.group(1)

        # Get text from switch start to next function-level close
        start_pos = smatch.end()
        # Find matching close brace
        depth = 1
        pos = start_pos
        while pos < len(text) and depth > 0:
            if text[pos] == '{':
                depth += 1
            elif text[pos] == '}':
                depth -= 1
            pos += 1
        body = text[start_pos:pos - 1]

        cases = re.findall(r'case\s+(\w+)\s*:', body)
        # Find any function calls in the switch body
        api_calls = re.findall(
            r'\b(\w+(?:Create|Init|Set|Process|Handle|Decode|Parse|Destroy|[a-z]_[a-z]))\s*\(',
            body,
        )
        # Also include generic function calls
        all_calls = re.findall(r'\b(\w+)\s*\(', body)
        # Filter out keywords
        filtered_calls = [c for c in all_calls if c not in (
            "if", "while", "for", "switch", "return", "sizeof", "case", "break",
        )]

        if cases and filtered_calls:
            result["harness_apis"].append({
                "selector": selector_var,
                "cases": cases[:8],
                "reachable_apis": api_calls[:8] if api_calls else filtered_calls[:8],
                "description": f"switch({selector_var}) dispatches to {len(cases)} cases",
            })


def _detect_font_table_dispatch(text: str, result: dict[str, Any]) -> None:
    """Detect font/SFNT table dispatch (Harfbuzz, OTS, etc.)."""
    # Pattern: hb_face_reference_table, ots_font_table, etc.
    table_patterns = [
        (r'hb_face_reference_table\s*\(\s*\w+\s*,\s*(\w+)\s*\)', "harfbuzz_table"),
        (r'hb_blob_make_writable\s*\(', "harfbuzz_blob"),
        (r'ots_font_table\s*\(\s*\w+\s*,\s*(\w+)\s*\)', "ots_table"),
        (r'(\w+)_table.*\btag\s*==\s*"(\w+)"', "sfnt_tag_check"),
    ]

    for pattern, kind in table_patterns:
        for match in re.finditer(pattern, text):
            if kind == "sfnt_tag_check":
                func = match.group(1)
                tag = match.group(2)
                result["table_selectors"].append({
                    "function": func,
                    "table_tag": tag,
                    "selector_kind": "tag",
                })
            elif kind in ("harfbuzz_table", "ots_table"):
                tag_var = match.group(1)
                result["table_selectors"].append({
                    "function": match.group(0)[:40],
                    "table_tag_var": tag_var,
                    "selector_kind": "harfbuzz_tag",
                })


def _detect_architecture_dispatch(text: str, result: dict[str, Any]) -> None:
    """Detect architecture/ISA selector dispatch (Capstone, etc.)."""
    # Pattern: CS_ARCH_*, cs_open(CS_ARCH_*, ...)
    arch_patterns = [
        (r'CS_ARCH_(\w+)', "capstone_arch"),
        (r'cs_open\s*\(\s*CS_ARCH_(\w+)', "capstone_cs_open"),
        (r'KS_ARCH_(\w+)', "keystone_arch"),
        (r'DISASM_ARCH_(\w+)', "disasm_arch"),
    ]

    for pattern, kind in arch_patterns:
        for match in re.finditer(pattern, text):
            arch = match.group(1)
            result["arch_selectors"].append({
                "architecture": arch,
                "selector_kind": kind,
                "constant": f"{kind.rsplit('_', 1)[0]}_{arch}",
            })


def _detect_mode_dispatch(text: str, result: dict[str, Any]) -> None:
    """Detect mode/state machine dispatch (OpenThread modes, etc.)."""
    mode_patterns = [
        (r'otInstanceInit\s*\(', "openthread_init"),
        (r'otIp6\s*\(', "openthread_ip6"),
        (r'otCli\s*\(', "openthread_cli"),
        (r'otNcp\s*\(', "openthread_ncp"),
        (r'otRadio\s*\(', "openthread_radio"),
    ]

    for pattern, kind in mode_patterns:
        for match in re.finditer(pattern, text):
            result["mode_selectors"].append({
                "mode": kind,
                "selector_kind": "api_dispatch",
                "function": match.group(0)[:40],
            })
