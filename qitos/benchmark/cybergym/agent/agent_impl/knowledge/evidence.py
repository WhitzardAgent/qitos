"""Evidence view — read-only snapshot of task evidence for pack detection.

This aggregates data that already exists in state at init time:
- task_id (contains project name in arvo:ID format)
- input_format (format_type, magic_bytes, consumption)
- corpus_files (file paths)
- harness_protocols (extracted from harness source)
- api_reachability (from static analysis bundle)
- crash_type (may be empty at init)

No new analysis is performed.  This is a projection, not a computation.

The key design principle: project_name → pack inference is a *programmatic*
evidence chain, not an LLM guess.  Keywords in description text can only
produce *candidate* decisions.  Confirmed requires hard evidence from
corpus magic, harness APIs, or source-backed format hints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceView:
    """Read-only view of task evidence for pack detection.

    Populated by build_evidence_view(state) — no LLM, no guessing.
    """

    task_id: str = ""
    project_name: str = ""
    vulnerability_description: str = ""
    crash_type: str = ""
    input_format_type: str = ""
    input_format_magic: str = ""
    harness_protocols: tuple[dict[str, Any], ...] = ()
    corpus_files: tuple[str, ...] = ()
    detected_magics: tuple[str, ...] = ()       # per-file magic from corpus
    harness_entry_symbol: str | None = None
    harness_api_calls: tuple[str, ...] = ()     # from api_reachability
    harness_input_contract: str = ""            # "buffer","packet","apdu",etc.
    harness_carrier_stack: tuple[str, ...] = () # protocol layer stack
    source_backed_hints: tuple[str, ...] = ()   # from static analysis


def _extract_project_name(task_id: str) -> str:
    """Extract project name from task_id.

    Arvo format: 'arvo:NNN' — project name must come from elsewhere.
    Task directories typically named by project.
    This is a best-effort extraction; packs should not rely on it alone.
    """
    return ""


def _read_corpus_magics(corpus_files: tuple[str, ...], limit: int = 10) -> tuple[str, ...]:
    """Read magic signatures from corpus files.

    Returns format identifiers like 'pdf', 'zip', 'png', etc.
    Only reads first 8 bytes of each file, up to `limit` files.
    """
    _MAGIC_MAP: list[tuple[bytes, str]] = [
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpeg"),
        (b"BM", "bmp"),
        (b"%PDF", "pdf"),
        (b"PK\x03\x04", "zip"),
        (b"RIFF", "wav"),
        (b"\x7fELF", "elf"),
        (b"GIF8", "gif"),
        (b"\x1f\x8b", "gzip"),
        (b"II\x2a\x00", "tiff"),
        (b"MM\x00\x2a", "tiff"),
        (b"\x00\x01\x00\x00", "ttf"),
        (b"OTTO", "otf"),
        (b"true", "ttf"),
        (b"wOFF", "woff"),
        (b"\x28\xb5\x2f\xfd", "zstd"),
    ]

    magics: list[str] = []
    for path in corpus_files[:limit]:
        try:
            with open(path, "rb") as f:
                header = f.read(8)
            for sig, fmt_name in _MAGIC_MAP:
                if header.startswith(sig):
                    if fmt_name not in magics:
                        magics.append(fmt_name)
                    break
        except (OSError, IOError):
            continue
    return tuple(magics)


def _extract_api_calls(api_reachability: dict[str, Any] | None) -> tuple[str, ...]:
    """Extract API call names from api_reachability result."""
    if not api_reachability:
        return ()
    calls: list[str] = []
    for harness_api in api_reachability.get("harness_apis", []):
        for api in harness_api.get("reachable_apis", []):
            if isinstance(api, str) and api not in calls:
                calls.append(api)
    return tuple(calls[:50])


def build_evidence_view(state: Any) -> EvidenceView:
    """Build an EvidenceView from current state.

    This is a pure projection — no new analysis, no LLM calls.
    All data comes from fields already populated during state_init.
    """
    # task_id
    task_id = str(getattr(state, "task_id", "") or "")

    # project_name — try state field first, then derive from task_root path
    project_name = str(getattr(state, "project_name", "") or "")
    if not project_name:
        # Derive from task_root directory name or task_id
        task_root = str(getattr(state, "task_root", "") or "")
        if task_root:
            project_name = os.path.basename(task_root.rstrip("/"))

    # vulnerability_description
    vuln_desc = str(getattr(state, "vulnerability_description", "") or "")

    # crash_type (may be empty at init)
    crash_type = str(getattr(state, "crash_type", "") or "")

    # input_format
    input_fmt = getattr(state, "input_format", None)
    input_format_type = str(getattr(input_fmt, "format_type", "") or "") if input_fmt else ""
    input_format_magic = str(getattr(input_fmt, "magic_bytes", "") or "") if input_fmt else ""

    # corpus_files
    corpus_files_list = list(getattr(state, "corpus_files", []) or [])
    corpus_files = tuple(corpus_files_list)

    # detected_magics — read from corpus file headers
    detected_magics = _read_corpus_magics(corpus_files)

    # harness_protocols
    harness_protocols = tuple(getattr(state, "harness_protocols", []) or [])

    # Extract harness-level info from protocols
    harness_input_contract = ""
    harness_carrier_stack: tuple[str, ...] = ()
    harness_entry_symbol = None
    if harness_protocols:
        proto = harness_protocols[0]
        harness_input_contract = str(proto.get("input_contract", "") or "")
        carrier = proto.get("carrier_stack", [])
        harness_carrier_stack = tuple(carrier) if isinstance(carrier, list) else ()

    # api_reachability from metadata
    metadata = getattr(state, "metadata", {}) or {}
    api_reach = metadata.get("api_reachability")
    harness_api_calls = _extract_api_calls(api_reach)

    # source-backed hints — from input_format field_provenance
    source_hints: list[str] = []
    if input_fmt and hasattr(input_fmt, "field_provenance"):
        provenance = getattr(input_fmt, "field_provenance", {}) or {}
        for field_name, source in provenance.items():
            if source and source not in ("default", "fallback", ""):
                hint = f"{field_name}={source}"
                if hint not in source_hints:
                    source_hints.append(hint)

    return EvidenceView(
        task_id=task_id,
        project_name=project_name,
        vulnerability_description=vuln_desc,
        crash_type=crash_type,
        input_format_type=input_format_type,
        input_format_magic=input_format_magic,
        harness_protocols=harness_protocols,
        corpus_files=corpus_files,
        detected_magics=detected_magics,
        harness_entry_symbol=harness_entry_symbol,
        harness_api_calls=harness_api_calls,
        harness_input_contract=harness_input_contract,
        harness_carrier_stack=harness_carrier_stack,
        source_backed_hints=tuple(source_hints),
    )
