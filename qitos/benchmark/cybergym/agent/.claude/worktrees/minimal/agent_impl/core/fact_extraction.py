"""Fact extraction helpers -- extracted from agent.py static methods.

These are pure functions that operate on simple types and CyberGymState,
with no dependency on the agent instance.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ...state import CyberGymState


def append_capped_fact(items: List[str], fact: str, *, limit: int = 6) -> List[str]:
    text = " ".join(str(fact or "").split()).strip()
    if not text:
        return list(items or [])
    filtered = [entry for entry in list(items or []) if entry != text]
    filtered.append(text)
    return filtered[-limit:]


def best_fact_snippet(content: str, *, limit: int = 160) -> str:
    for raw_line in str(content or "").splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if line.startswith(("//", "#", "/*", "*", "*/")):
            continue
        return line
    return " ".join(str(content or "").split())


def extract_structured_facts_from_content(content: str, path: str) -> List[str]:
    """Deterministically extract structured facts from READ content.

    Extracts #define constants with numeric values, buffer size
    declarations, struct field offsets, variable types, and function
    signatures -- the facts most likely to be lost in LLM-based
    context compaction or needed for PoC byte-level construction.
    """
    if not content or not path:
        return []
    facts: List[str] = []
    # #define constants with numeric values
    for m in re.finditer(r'#define\s+(\w+)\s+(\d+)', content):
        facts.append(f"const: {m.group(1)} = {m.group(2)} (in {path})")
    # Buffer/size declarations: type name[SIZE]
    for m in re.finditer(r'(?:char|uint\d+_t|int|size_t|unsigned)\s+\w+\[(\d+)\]', content):
        facts.append(f"buffer_size: {m.group(1)} (in {path})")
    # Struct field access patterns: pde+8, tiffp+4
    seen_offsets = set()
    for m in re.finditer(r'(\w+)\+(\d+)\)', content):
        var, off = m.group(1), m.group(2)
        key = f"{var}+{off}"
        if int(off) > 0 and int(off) < 1000 and key not in seen_offsets:
            seen_offsets.add(key)
            facts.append(f"field_offset: {var}+{off} = {off} (in {path})")
    # Key variable types for overflow analysis: unsigned long oval, size_t n
    for m in re.finditer(r'(unsigned\s+(?:long|int|short|char))\s+(\w+)', content):
        facts.append(f"var_type: {m.group(2)} = {m.group(1)} (in {path})")
    # Function signatures (simplified)
    for m in re.finditer(r'(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{', content):
        fname = m.group(1)
        if fname not in ("if", "for", "while", "switch", "return", "sizeof"):
            facts.append(f"func: {fname} (in {path})")
    return facts[:12]


def extract_poc_paths_from_bash(command: str, state: CyberGymState) -> List[str]:
    """Extract PoC file paths mentioned in a BASH command string.

    Only matches paths under pocs/ that look like output targets,
    not source paths.  Avoids registering paths that the command
    reads from (e.g., ``cp source target``).
    """
    if not command or not state.workspace_root:
        return []
    # Match output redirection targets and python write paths
    paths: List[str] = []
    seen: set[str] = set()
    # Redirection: > pocs/foo or >> pocs/foo
    for m in re.finditer(r'[>]\s*([^\s;&|]+pocs[^\s;&|]*)', command):
        p = m.group(1).strip("'\"")
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    # Python open/write patterns: open("pocs/foo", "w")
    for m in re.finditer(r'open\(["\']([^"\']*pocs[^"\']*)["\']', command):
        p = m.group(1)
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths
