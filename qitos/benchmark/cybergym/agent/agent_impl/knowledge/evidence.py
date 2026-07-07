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


# ---------------------------------------------------------------------------
# Eager pack selection — runs at init_state time
# ---------------------------------------------------------------------------

_MODE_ORDER = {"unconfirmed": 0, "candidate": 1, "confirmed": 2}


def eager_pack_select(state: Any) -> dict[str, Any]:
    """Run pack selection at init_state time.

    Returns a PackMode dict for storage on state.pack_mode.
    Uses evidence already populated during state_init (corpus magics,
    harness protocols, input_format, etc.).
    """
    try:
        from .registry import get_knowledge_registry

        registry = get_knowledge_registry()
        if registry.is_empty():
            return {"mode": "unconfirmed", "pack_id": "", "detection_score": 0.0,
                    "positive_evidence_ids": (), "missing_evidence": (),
                    "confirmed_at_step": -1, "upgrade_history": ()}

        evidence = build_evidence_view(state)
        selected = registry.select_packs(evidence)

        if not selected:
            return {"mode": "unconfirmed", "pack_id": "", "detection_score": 0.0,
                    "positive_evidence_ids": (), "missing_evidence": (),
                    "confirmed_at_step": -1, "upgrade_history": ()}

        pack, det_result = selected[0]

        # Map detection decision to mode
        if det_result.decision == "confirmed" and det_result.score >= 0.7:
            mode = "confirmed"
        elif det_result.decision == "candidate" and det_result.score >= 0.2:
            mode = "candidate"
        else:
            mode = "unconfirmed"

        step = int(getattr(state, "current_step", 0) or 0)
        history = ()
        if mode != "unconfirmed":
            history = (f"unconfirmed->{mode}@init_step{step}",)

        return {
            "mode": mode,
            "pack_id": pack.descriptor.pack_id if mode != "unconfirmed" else "",
            "detection_score": det_result.score,
            "positive_evidence_ids": det_result.positive_evidence_ids,
            "missing_evidence": det_result.missing_evidence,
            "confirmed_at_step": step if mode != "unconfirmed" else -1,
            "upgrade_history": history,
        }

    except Exception:
        # Never crash init_state — return safe default
        return {"mode": "unconfirmed", "pack_id": "", "detection_score": 0.0,
                "positive_evidence_ids": (), "missing_evidence": (),
                "confirmed_at_step": -1, "upgrade_history": ()}


def maybe_upgrade_pack_mode(state: Any) -> bool:
    """Re-run detection with current state. Only upgrades, never downgrades.

    When upgraded to confirmed, also runs parse→derive_contract→plan pipeline
    and stores results in state.metadata.

    Returns True if pack_mode was upgraded.
    """
    current = getattr(state, "pack_mode", {}) or {}
    current_mode = current.get("mode", "unconfirmed")

    try:
        from .registry import get_knowledge_registry

        registry = get_knowledge_registry()
        if registry.is_empty():
            return False

        evidence = build_evidence_view(state)
        selected = registry.select_packs(evidence)

        if not selected:
            return False

        pack, det_result = selected[0]

        # Determine new mode
        if det_result.decision == "confirmed" and det_result.score >= 0.7:
            new_mode = "confirmed"
        elif det_result.decision == "candidate" and det_result.score >= 0.2:
            new_mode = "candidate"
        else:
            return False

        # Only upgrade
        if _MODE_ORDER.get(new_mode, 0) <= _MODE_ORDER.get(current_mode, 0):
            return False

        # Upgrade
        step = int(getattr(state, "current_step", 0) or 0)
        upgrade_record = f"{current_mode}->{new_mode}@step{step}"
        history = list(current.get("upgrade_history", ()))
        history.append(upgrade_record)

        state.pack_mode = {
            "mode": new_mode,
            "pack_id": pack.descriptor.pack_id,
            "detection_score": det_result.score,
            "positive_evidence_ids": det_result.positive_evidence_ids,
            "missing_evidence": det_result.missing_evidence,
            "confirmed_at_step": step,
            "upgrade_history": tuple(history),
        }

        # If upgraded to confirmed, activate full pipeline
        if new_mode == "confirmed":
            _activate_confirmed_pack(state, pack, evidence)

        # Force observation refresh
        try:
            from ..core.runtime_context_contract import bump_context_revision
            bump_context_revision(state, "domain_packs")
        except Exception:
            pass

        return True

    except Exception:
        return False


def activate_pack_from_tool(state: Any, pack_id: str, confidence: str, evidence_text: str) -> dict[str, Any]:
    """Activate a pack from the confirm_format tool.

    Validates the pack_id against the registry, updates state.pack_mode,
    and optionally runs the full pipeline for confirmed mode.

    Returns updated PackMode dict.
    """
    from .registry import get_knowledge_registry

    current = getattr(state, "pack_mode", {}) or {}
    current_mode = current.get("mode", "unconfirmed")

    registry = get_knowledge_registry()

    # Handle "unknown" — reset to unconfirmed
    if pack_id == "unknown":
        if _MODE_ORDER.get("unconfirmed", 0) < _MODE_ORDER.get(current_mode, 0):
            # Don't downgrade
            return dict(current)
        state.pack_mode = {
            "mode": "unconfirmed", "pack_id": "", "detection_score": 0.0,
            "positive_evidence_ids": (), "missing_evidence": (),
            "confirmed_at_step": -1, "upgrade_history": current.get("upgrade_history", ()),
        }
        try:
            from ..core.runtime_context_contract import bump_context_revision
            bump_context_revision(state, "domain_packs")
        except Exception:
            pass
        return dict(state.pack_mode)

    # Look up pack
    pack = registry.get_pack(pack_id)
    if pack is None:
        # Pack not registered — still respect agent's confidence level.
        # Don't show "registered_pack" as missing evidence; the agent
        # has confirmed the format and the pack registry is an internal detail.
        step = int(getattr(state, "current_step", 0) or 0)
        mode = "confirmed" if confidence == "confirmed" else "candidate"
        score = 0.7 if confidence == "confirmed" else 0.3
        state.pack_mode = {
            "mode": mode, "pack_id": pack_id, "detection_score": score,
            "positive_evidence_ids": ("agent_inference",),
            "missing_evidence": (),
            "confirmed_at_step": step if mode == "confirmed" else -1,
            "upgrade_history": current.get("upgrade_history", ())
                              + (f"{current_mode}->{mode}@step{step}",),
        }
        try:
            from ..core.runtime_context_contract import bump_context_revision
            bump_context_revision(state, "domain_packs")
        except Exception:
            pass
        return dict(state.pack_mode)

    # Determine mode from confidence parameter
    new_mode = "confirmed" if confidence == "confirmed" else "candidate"

    # Only upgrade
    if _MODE_ORDER.get(new_mode, 0) <= _MODE_ORDER.get(current_mode, 0):
        # Already at this level or higher — update pack_id if different
        if current.get("pack_id") != pack_id:
            # Allow switching pack at same level
            pass
        else:
            return dict(current)

    step = int(getattr(state, "current_step", 0) or 0)
    upgrade_record = f"{current_mode}->{new_mode}@step{step}"
    history = list(current.get("upgrade_history", ()))
    history.append(upgrade_record)

    score = 0.9 if new_mode == "confirmed" else 0.5
    state.pack_mode = {
        "mode": new_mode,
        "pack_id": pack_id,
        "detection_score": score,
        "positive_evidence_ids": ("agent_confirm",) + (() if not evidence_text else ("agent_evidence",)),
        "missing_evidence": (),
        "confirmed_at_step": step if new_mode == "confirmed" else -1,
        "upgrade_history": tuple(history),
    }

    # Activate full pipeline for confirmed
    if new_mode == "confirmed":
        ev = build_evidence_view(state)
        _activate_confirmed_pack(state, pack, ev)

    try:
        from ..core.runtime_context_contract import bump_context_revision
        bump_context_revision(state, "domain_packs")
    except Exception:
        pass

    return dict(state.pack_mode)


def _activate_confirmed_pack(state: Any, pack: Any, evidence: EvidenceView) -> None:
    """Run full pack pipeline for confirmed mode and store results."""
    metadata = getattr(state, "metadata", {})
    if not isinstance(metadata, dict):
        return

    try:
        # Parse best seed
        seed_path = _find_best_seed(state)
        if seed_path:
            try:
                with open(seed_path, "rb") as f:
                    seed_bytes = f.read()
                parse_result = pack.parse(seed_bytes)
                # Store serialized parse result
                metadata["pack_parse_result"] = {
                    "status": parse_result.status,
                    "carrier_family": parse_result.carrier_family,
                    "version": parse_result.version,
                    "node_count": parse_result.node_count,
                    "field_map": {
                        name: {"offset": fi.offset, "width": fi.width,
                               "derived": fi.derived, "protected": fi.protected}
                        for name, fi in list(parse_result.field_map.items())[:30]
                    },
                }
            except Exception:
                pass

        # Derive carrier contract
        try:
            contract = pack.derive_contract(parse_result if 'parse_result' in dir() else None, None)
            metadata["carrier_contract"] = {
                "format_id": contract.format_id,
                "seed_required": contract.seed_required,
                "minimal_seed_size": contract.minimal_seed_size,
                "required_fields": list(contract.required_fields)[:10],
                "derived_fields": list(contract.derived_fields)[:10],
                "protected_fields": list(contract.protected_fields)[:10],
                "harness_acceptance_hints": list(contract.harness_acceptance_hints)[:5],
            }
        except Exception:
            pass

    except Exception:
        pass


def _find_best_seed(state: Any) -> str:
    """Find the best seed file path from state."""
    # Check recipe seed first
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()
    seed_path = recipe.get("carrier", {}).get("seed_path", "")
    if seed_path:
        import os
        if os.path.exists(seed_path):
            return seed_path

    # Fall back to first corpus file
    corpus_files = list(getattr(state, "corpus_files", []) or [])
    for f in corpus_files:
        import os
        if os.path.exists(f):
            return f

    return ""
