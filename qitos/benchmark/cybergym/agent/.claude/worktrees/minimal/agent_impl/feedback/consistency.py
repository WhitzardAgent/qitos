"""Consistency guard — checks alignment between task/harness/format/PoC/submission.

Returns structured signals that the agent uses as hard blockers (severity=block)
or soft warnings (severity=warn).  Only blocks when there is clear evidence
of a mismatch; uncertain cases produce warnings.

This module does NOT generate or modify PoCs.  It only inspects and signals.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState


# Known magic byte prefixes: hex string -> (format_label, min_bytes)
_KNOWN_MAGICS: dict[str, tuple[str, int]] = {
    "7f454c46": ("elf", 4),
    "cefaedfe": ("pe", 4),       # PE little-endian
    "feedface": ("mach-o", 4),   # Mach-O 32-bit
    "feedfacf": ("mach-o", 4),   # Mach-O 64-bit
    "cffaedfe": ("pe", 4),       # PE big-endian
    "504b0304": ("zip", 4),
    "25504446": ("pdf", 4),
    "89504e47": ("png", 4),
    "ffd8ffe0": ("jpeg", 4),
    "ffd8ffe1": ("jpeg", 4),
    "47494638": ("gif", 4),
    "000000": ("mp4", 3),
    "1a45dfa3": ("webm", 4),
    "0061": ("wasm", 2),
}


def evaluate_consistency(
    *,
    state: CyberGymState,
    poc_path: str | None = None,
    submit_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return consistency signals between task/harness/format/PoC/submission.

    Each signal has: signal_id, kind, severity (block|warn), summary,
    repair_action, blocks_submit, evidence.
    """
    signals: list[dict[str, Any]] = []

    # 1. Magic bytes vs PoC header
    _check_magic_consistency(state, poc_path, signals)

    # 2. Container structure vs PoC structure
    _check_container_consistency(state, poc_path, signals)

    # 3. Endpoint scope vs transcript/recipe
    _check_endpoint_scope_consistency(state, signals)

    # 4. Carrier stack coverage
    _check_carrier_stack_consistency(state, signals)

    # 5. Architecture selector alignment
    _check_architecture_consistency(state, signals)

    # 6. Submit-result-driven checks
    if submit_result:
        _check_submit_consistency(state, submit_result, signals)

    return signals[:10]


def _stable_signal_id(material: str) -> str:
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"cs_{h}"


def _poc_header_hex(poc_path: str, n: int = 16) -> str:
    """Read first n bytes of a file and return as hex string (no spaces)."""
    try:
        with open(poc_path, "rb") as f:
            header = f.read(n)
        return header.hex()
    except (OSError, ValueError):
        return ""


def _infer_format_from_hex(hex_header: str) -> str:
    """Return format label if hex header matches a known magic, else ''."""
    for magic_hex, (label, nbytes) in _KNOWN_MAGICS.items():
        if hex_header[:len(magic_hex)] == magic_hex:
            return label
    return ""


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------

def _check_magic_consistency(
    state: CyberGymState,
    poc_path: str | None,
    signals: list[dict[str, Any]],
) -> None:
    """Compare expected magic bytes with PoC header."""
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    expected_magic = str(getattr(fmt, "magic_bytes", "") or "").strip().replace(" ", "").lower()
    if not expected_magic:
        return

    if not poc_path:
        return

    workspace = str(getattr(state, "workspace_root", "") or "")
    full_path = os.path.join(workspace, poc_path) if workspace else poc_path
    if not os.path.isfile(full_path):
        return

    header_hex = _poc_header_hex(full_path)
    if not header_hex:
        return

    # Compare magic prefixes
    magic_len = len(expected_magic)
    actual_prefix = header_hex[:magic_len]

    if actual_prefix != expected_magic:
        # Determine expected format for summary
        expected_fmt = getattr(fmt, "format_type", "") or _infer_format_from_hex(expected_magic)
        actual_fmt = _infer_format_from_hex(header_hex)

        summary = (
            f"Expected magic {expected_magic} ({expected_fmt}), "
            f"but PoC starts with {actual_prefix}"
        )
        if actual_fmt:
            summary += f" ({actual_fmt})"

        signals.append({
            "signal_id": _stable_signal_id(f"magic|{expected_magic}|{actual_prefix}"),
            "kind": "wrong_format",
            "severity": "block",
            "summary": summary,
            "repair_action": f"Fix PoC header to start with magic bytes {expected_magic}",
            "blocks_submit": True,
            "evidence": [{"expected_magic": expected_magic, "actual_prefix": actual_prefix}],
        })


def _check_container_consistency(
    state: CyberGymState,
    poc_path: str | None,
    signals: list[dict[str, Any]],
) -> None:
    """Check that PoC structure matches InputFormatModel.container_structure."""
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    container = str(getattr(fmt, "container_structure", "") or "").strip()
    if not container:
        return

    # Check if there are nested layers mentioned (e.g., "CFF2 inside SFNT inside OTF")
    layers = [l.strip() for l in container.split(" inside ") if l.strip()]

    # If PoC path is available, check file size against multi-layer expectation
    if poc_path and layers:
        workspace = str(getattr(state, "workspace_root", "") or "")
        full_path = os.path.join(workspace, poc_path) if workspace else poc_path
        try:
            file_size = os.path.getsize(full_path)
        except OSError:
            file_size = 0

        # Multi-layer container with very small file suggests missing wrappers
        if len(layers) > 1 and 0 < file_size < 64:
            signals.append({
                "signal_id": _stable_signal_id(f"container|{container}"),
                "kind": "wrong_format_scope",
                "severity": "block",
                "summary": (
                    f"Input expects nested container ({container}), "
                    f"but PoC is only {file_size} bytes"
                ),
                "repair_action": (
                    f"Build PoC with proper container layers: "
                    + " wrapping ".join(reversed(layers))
                ),
                "blocks_submit": True,
                "evidence": [{"container_structure": container, "file_size": file_size}],
            })


def _check_endpoint_scope_consistency(
    state: CyberGymState,
    signals: list[dict[str, Any]],
) -> None:
    """Check endpoint_scope alignment with transcript/recipe requirements."""
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    consumption = getattr(fmt, "consumption", None)
    if not consumption:
        return

    endpoint_scope = str(getattr(consumption, "endpoint_scope", "") or "").strip()
    transcript_required = getattr(consumption, "transcript_required", False)
    carrier_stack = list(getattr(consumption, "carrier_stack", []) or [])
    required_wrappers = list(getattr(consumption, "required_wrappers", []) or [])

    # Check if transcript is required but no transcript plan exists
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    active_tr = [t for t in transcripts if t.get("status") in ("active", "candidate")]

    if transcript_required and not active_tr:
        scope_desc = f"endpoint_scope={endpoint_scope}" if endpoint_scope else "multi-step protocol"
        signals.append({
            "signal_id": _stable_signal_id(f"transcript_required|{endpoint_scope}"),
            "kind": "scope_mismatch",
            "severity": "block",
            "summary": (
                f"Harness expects {scope_desc} (transcript_required), "
                f"but no transcript plan exists"
            ),
            "repair_action": (
                f"Build protocol_transcript_plan with ordered steps "
                f"matching the {endpoint_scope} harness"
            ),
            "blocks_submit": True,
            "evidence": [{"endpoint_scope": endpoint_scope, "transcript_required": True}],
        })

    # Check multi-stage scope against single-buffer recipe
    if endpoint_scope in ("socket", "callback", "apdu", "multi_stage", "packet"):
        recipe = {}
        if hasattr(state, "get_poc_recipe"):
            recipe = state.get_poc_recipe()
        carrier = recipe.get("carrier", {}) if isinstance(recipe, dict) else {}
        # If recipe is empty or has no transcript steps, warn
        if not carrier.get("transcript_steps") and not active_tr:
            signals.append({
                "signal_id": _stable_signal_id(f"scope_single|{endpoint_scope}"),
                "kind": "scope_mismatch",
                "severity": "warn",
                "summary": (
                    f"Harness endpoint_scope={endpoint_scope} likely needs "
                    f"ordered multi-step input; current recipe has no transcript"
                ),
                "repair_action": (
                    f"Read the harness source to confirm input protocol, "
                    f"then build a transcript plan"
                ),
                "blocks_submit": False,
                "evidence": [{"endpoint_scope": endpoint_scope}],
            })


def _check_carrier_stack_consistency(
    state: CyberGymState,
    signals: list[dict[str, Any]],
) -> None:
    """Check that carrier_stack wrappers are covered by the recipe."""
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    consumption = getattr(fmt, "consumption", None)
    if not consumption:
        return

    carrier_stack = list(getattr(consumption, "carrier_stack", []) or [])
    required_wrappers = list(getattr(consumption, "required_wrappers", []) or [])

    if not carrier_stack and not required_wrappers:
        return

    # Check against recipe
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()
    recipe_wrappers = recipe.get("wrappers", []) if isinstance(recipe, dict) else []
    recipe_carriers = recipe.get("carriers", []) if isinstance(recipe, dict) else []

    all_needed = set(carrier_stack + required_wrappers)
    all_present = set(str(w) for w in (recipe_wrappers + recipe_carriers))

    missing = all_needed - all_present
    if missing and len(carrier_stack) > 1:
        # Multiple carrier layers and some are missing from recipe
        signals.append({
            "signal_id": _stable_signal_id(f"carrier_stack|{'|'.join(sorted(missing))}"),
            "kind": "wrong_format_scope",
            "severity": "block" if len(carrier_stack) > 1 else "warn",
            "summary": (
                f"Carrier stack requires {', '.join(carrier_stack)}, "
                f"but recipe is missing outer layers: {', '.join(sorted(missing))}"
            ),
            "repair_action": (
                f"Wrap PoC payload with missing carrier layers: "
                f"{', '.join(sorted(missing))}"
            ),
            "blocks_submit": len(carrier_stack) > 1,
            "evidence": [
                {"carrier_stack": carrier_stack, "missing": sorted(missing)},
            ],
        })


def _check_architecture_consistency(
    state: CyberGymState,
    signals: list[dict[str, Any]],
) -> None:
    """Check architecture selector alignment with rewrite plans and objectives."""
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    consumption = getattr(fmt, "consumption", None)
    if not consumption:
        return

    arch_selector = str(getattr(consumption, "architecture_selector", "") or "").strip()
    if not arch_selector or arch_selector == "unknown":
        return

    # Check against rewrite plans
    rewrites = list(getattr(state, "structured_rewrite_plans", []) or [])
    for rw in rewrites:
        rw_arch = str(rw.get("architecture", "") or "").strip()
        if rw_arch and rw_arch != arch_selector:
            signals.append({
                "signal_id": _stable_signal_id(f"arch|{arch_selector}|{rw_arch}"),
                "kind": "wrong_arch",
                "severity": "block",
                "summary": (
                    f"Harness expects architecture {arch_selector}, "
                    f"but rewrite plan uses {rw_arch}"
                ),
                "repair_action": f"Update rewrite plan to use architecture {arch_selector}",
                "blocks_submit": True,
                "evidence": [
                    {"harness_arch": arch_selector, "rewrite_arch": rw_arch},
                ],
            })
            break  # One signal is enough

    # Check against active objectives
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    for obj in objectives:
        obj_arch = str(obj.get("architecture", "") or "").strip()
        if obj_arch and obj_arch != arch_selector:
            signals.append({
                "signal_id": _stable_signal_id(f"arch_obj|{arch_selector}|{obj_arch}"),
                "kind": "wrong_arch",
                "severity": "warn",
                "summary": (
                    f"Harness expects architecture {arch_selector}, "
                    f"but objective targets {obj_arch}"
                ),
                "repair_action": f"Align objective with harness architecture {arch_selector}",
                "blocks_submit": False,
                "evidence": [
                    {"harness_arch": arch_selector, "objective_arch": obj_arch},
                ],
            })
            break


# ------------------------------------------------------------------
# Pre-submit helpers (split from FeedbackMixin._pre_submit_validate)
# ------------------------------------------------------------------

def pre_submit_sanity_check(state, poc_path: str) -> str:
    """Run carrier sanity check; returns '' if pass or skip, else error string."""
    import os as _os
    from .poc_sanity import inspect_poc_bytes

    fmt = getattr(state, "input_format", None)
    if not fmt:
        return ""
    magic_str = str(getattr(fmt, "magic_bytes", "") or "").strip()
    fmt_type = str(getattr(fmt, "format_type", "") or "").strip()
    if not magic_str and not fmt_type:
        return ""

    # Resolve the full PoC path
    workspace = str(getattr(state, "workspace_root", "") or "")
    full_path = _os.path.join(workspace, poc_path) if workspace else poc_path
    if not _os.path.isfile(full_path):
        return ""

    # Determine expected format for sanity checker
    expected_format = fmt_type or ""
    poc_strategy = str(getattr(state, "poc_strategy", "") or "")
    if not expected_format and poc_strategy in ("corpus_mutate", "binary_python"):
        expected_format = ""

    # Get seed path from recipe if available
    recipe = (state.metadata or {}).get("poc_recipe", {})
    carrier = recipe.get("carrier", {}) if isinstance(recipe, dict) else {}
    seed_path = carrier.get("seed_path") or None

    # Run sanity checker
    try:
        result = inspect_poc_bytes(
            full_path,
            expected_format=expected_format,
            seed_path=seed_path,
        )
    except Exception:
        return ""

    # Store result in state metadata for observation rendering
    if hasattr(state, "metadata") and isinstance(state.metadata, dict):
        state.metadata["last_poc_sanity"] = result.to_dict()

    if not result.passed:
        fail_issues = [i for i in result.issues if i.severity == "fail"]
        messages = [f"{i.category}: {i.message}" for i in fail_issues[:3]]
        repair = fail_issues[0].repair_hint if fail_issues and fail_issues[0].repair_hint else ""
        repair_suffix = f" {repair}" if repair else ""
        return (
            f"CARRIER_SANITY_FAIL: PoC carrier is invalid. "
            f"{'; '.join(messages)}.{repair_suffix}"
        )

    # Warnings: soft, don't block but inform
    warn_issues = [i for i in result.issues if i.severity == "warn"]
    if warn_issues:
        warn_msgs = [f"{i.category}: {i.message}" for i in warn_issues[:2]]
        if hasattr(state, "metadata") and isinstance(state.metadata, dict):
            state.metadata.setdefault("poc_sanity_warnings", [])
            state.metadata["poc_sanity_warnings"].extend(warn_msgs)

    return ""


def run_consistency_guard(state, poc_path: str) -> str:
    """Run consistency guard; returns '' if pass, else 'CONSISTENCY_BLOCK: ...'."""
    try:
        signals = evaluate_consistency(state=state, poc_path=poc_path)
    except Exception:
        return ""

    if not signals:
        return ""

    # Store signals in state and bump revision
    if hasattr(state, "consistency_signals"):
        state.consistency_signals = signals[:10]
        from ..core.runtime_context_contract import bump_context_revision
        bump_context_revision(state, "consistency_signals")

    # Check for blocking signals
    for sig in signals:
        if sig.get("blocks_submit"):
            return (
                "CONSISTENCY_BLOCK: "
                f"{sig.get('summary', 'PoC does not match harness contract')}; "
                f"repair={sig.get('repair_action', '')}"
            )

    return ""


def append_consistency_negative_evidence(
    state,
    gate: str,
    candidate_id: str,
    ranked_path_id: str,
) -> None:
    """After a failed submit, check consistency signals and append
    scoped negative evidence for format/harness/scope mismatches."""
    # Only check on no-crash or format-related gates
    if gate not in ("no_crash_unknown", "carrier_parse", "timeout_not_crash"):
        return

    signals = list(getattr(state, "consistency_signals", []) or [])
    if not signals:
        return

    for sig in signals:
        kind = sig.get("kind", "")
        severity = sig.get("severity", "")
        sig_id = sig.get("signal_id", "")
        summary = sig.get("summary", "")

        # Map signal kind to negative evidence kind
        ne_kind = ""
        avoid = ""
        if kind == "wrong_format":
            ne_kind = "wrong_format_scope"
            avoid = "same_carrier_format"
        elif kind == "wrong_format_scope":
            ne_kind = "wrong_format_scope"
            avoid = "same_carrier_format_without_wrapper"
        elif kind == "scope_mismatch":
            ne_kind = "transcript_endpoint_mismatch"
            avoid = "same_single_buffer_without_transcript"
        elif kind == "wrong_arch":
            ne_kind = "wrong_harness_binary"
            avoid = "same_architecture_mismatch"

        if not ne_kind:
            continue

        state.append_negative_evidence(
            kind=ne_kind,
            candidate_id=candidate_id,
            ranked_path_id=ranked_path_id,
            consistency_signal_id=sig_id,
            summary=summary[:200],
            avoid_next=avoid,
        )


def _check_submit_consistency(
    state: CyberGymState,
    submit_result: dict[str, Any],
    signals: list[dict[str, Any]],
) -> None:
    """Derive consistency signals from submit result (no-crash / parse error)."""
    status = str(submit_result.get("status", "") or "").lower()
    error_msg = str(submit_result.get("error", "") or "").lower()

    # Parser reject or format error from submit
    if "parse" in error_msg or "format" in error_msg or "header" in error_msg:
        fmt = getattr(state, "input_format", None)
        fmt_type = str(getattr(fmt, "format_type", "") or "") if fmt else ""
        signals.append({
            "signal_id": _stable_signal_id(f"submit_parse|{error_msg[:40]}"),
            "kind": "wrong_format_scope",
            "severity": "block",
            "summary": (
                f"Submit returned parse/format error: {error_msg[:100]}"
                + (f" (expected format: {fmt_type})" if fmt_type else "")
            ),
            "repair_action": "Verify PoC matches harness input format; check container/wrapper layers",
            "blocks_submit": True,
            "evidence": [{"submit_error": error_msg[:200]}],
        })

    # No-crash with transcript required but no transcript
    if "no_crash" in status or "no crash" in status:
        consumption = None
        fmt = getattr(state, "input_format", None)
        if fmt:
            consumption = getattr(fmt, "consumption", None)
        if consumption and getattr(consumption, "transcript_required", False):
            transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
            if not transcripts:
                signals.append({
                    "signal_id": _stable_signal_id("submit_no_crash_transcript"),
                    "kind": "scope_mismatch",
                    "severity": "warn",
                    "summary": "No crash but harness requires transcript; likely scope mismatch",
                    "repair_action": "Build transcript plan before resubmitting",
                    "blocks_submit": False,
                    "evidence": [{"transcript_required": True, "no_transcript": True}],
                })
