"""Transcript runtime — state-level helpers for protocol transcript plans.

This module does NOT execute any real protocol or network I/O.
It provides helpers for:
- Selecting the active transcript plan
- Comparing the current PoC recipe against transcript requirements
- Producing gap summaries that feed into Next Action priority
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState


def select_active_transcript_plan(state: CyberGymState) -> dict[str, Any]:
    """Return the active (or first candidate) transcript plan, or empty dict."""
    plans = list(getattr(state, "protocol_transcript_plans", []) or [])
    for plan in plans:
        if plan.get("status") == "active":
            return plan
    # Fall back to first candidate
    for plan in plans:
        if plan.get("status") == "candidate":
            return plan
    return plans[0] if plans else {}


def transcript_gap_for_current_recipe(state: CyberGymState) -> dict[str, Any]:
    """Compare current poc_recipe with selected transcript plan.

    Returns a dict with:
      - missing_steps: list of step roles not covered by the recipe
      - wrong_order: True if recipe steps violate required_order
      - wrong_scope: True if recipe is single raw bytes but transcript needs multi-step
      - summary: human-readable one-line
    """
    plan = select_active_transcript_plan(state)
    if not plan:
        return {
            "missing_steps": [],
            "wrong_order": False,
            "wrong_scope": False,
            "summary": "",
        }

    steps = list(plan.get("steps", []) or [])
    required_order = list(plan.get("required_order", []) or [])
    transcript_id = plan.get("transcript_id", "")

    # Examine the current recipe
    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()

    recipe_steps = []
    recipe_transcript_steps = []
    if isinstance(recipe, dict):
        recipe_steps = list(recipe.get("steps", []) or [])
        carrier = recipe.get("carrier", {}) or {}
        recipe_transcript_steps = list(carrier.get("transcript_steps", []) or [])

    # Determine missing steps
    step_roles_in_plan = [s.get("role", "") for s in steps]
    step_roles_in_recipe = []

    # Collect roles from recipe steps
    for rs in recipe_steps:
        if isinstance(rs, dict):
            role = str(rs.get("role", "") or rs.get("step_role", "") or "")
            if role:
                step_roles_in_recipe.append(role)
    for rs in recipe_transcript_steps:
        if isinstance(rs, dict):
            role = str(rs.get("role", "") or rs.get("step_role", "") or "")
            if role:
                step_roles_in_recipe.append(role)

    missing_steps = [r for r in step_roles_in_plan if r not in step_roles_in_recipe]

    # Check order violation
    wrong_order = False
    if required_order and step_roles_in_recipe:
        # Build role order from plan steps: map role -> position based on step order
        role_order = {}
        for idx, step in enumerate(steps):
            role = step.get("role", "")
            if role:
                role_order[role] = idx
        # Check if recipe roles appear in monotonically increasing order
        recipe_positions = []
        for role in step_roles_in_recipe:
            if role in role_order:
                recipe_positions.append(role_order[role])
        for i in range(1, len(recipe_positions)):
            if recipe_positions[i] < recipe_positions[i - 1]:
                wrong_order = True
                break

    # Check scope mismatch: recipe is single raw bytes but transcript needs multi-step
    wrong_scope = False
    if len(steps) >= 2:
        # If recipe has no transcript steps and no multi-step indication, it's wrong scope
        if not recipe_transcript_steps and not recipe_steps:
            wrong_scope = True
        elif recipe_transcript_steps and len(recipe_transcript_steps) < len(steps):
            # Partial coverage
            wrong_scope = True

    # Build summary
    parts = []
    if missing_steps:
        parts.append(f"missing steps: {', '.join(missing_steps)}")
    if wrong_order:
        parts.append("step order violated")
    if wrong_scope:
        parts.append(
            f"recipe has raw bytes, but transcript {transcript_id} requires "
            + " -> ".join(step_roles_in_plan)
        )

    summary = "; ".join(parts) if parts else ""

    return {
        "missing_steps": missing_steps,
        "wrong_order": wrong_order,
        "wrong_scope": wrong_scope,
        "summary": summary,
    }


def fill_harness_consumption_extensions(state: CyberGymState) -> None:
    """Fill endpoint_scope, carrier_stack, transcript_required, etc. into
    HarnessConsumptionModel from harness analysis and static analysis results.

    Called after harness analysis and structured analysis bundle sync are complete.
    """
    fmt = getattr(state, "input_format", None)
    if not fmt:
        return

    consumption = getattr(fmt, "consumption", None)
    if not consumption:
        return

    # Infer endpoint_scope from consumption pattern
    if not getattr(consumption, "endpoint_scope", ""):
        pattern = str(getattr(consumption, "pattern", "") or "").lower()
        patterns = [p.lower() for p in (getattr(consumption, "patterns", []) or [])]

        scope = _infer_endpoint_scope(pattern, patterns)
        if scope:
            consumption.endpoint_scope = scope

    # Set transcript_required based on endpoint_scope and transcript plans
    scope = str(getattr(consumption, "endpoint_scope", "") or "")
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    active_tr = [t for t in transcripts if t.get("status") in ("active", "candidate")]

    if active_tr or scope in ("socket", "callback", "apdu", "multi_stage"):
        consumption.transcript_required = True

    # Populate transcript_evidence from protocol transcript plans
    if active_tr and not getattr(consumption, "transcript_evidence", []):
        evidence_list = []
        for tr in active_tr[:3]:
            steps = tr.get("steps", [])
            evidence_list.append({
                "transcript_id": tr.get("transcript_id", ""),
                "required_order": tr.get("required_order", []),
                "step_count": len(steps),
                "scope": tr.get("harness_endpoint_scope", ""),
            })
        consumption.transcript_evidence = evidence_list

    # Infer carrier_stack from container_structure if not already set
    if not getattr(consumption, "carrier_stack", []):
        container = str(getattr(fmt, "container_structure", "") or "")
        if container:
            layers = [l.strip() for l in container.split(" inside ") if l.strip()]
            if layers:
                consumption.carrier_stack = list(reversed(layers))

    # Infer architecture_selector from description_analysis or crash_type
    if not getattr(consumption, "architecture_selector", ""):
        desc = {}
        if hasattr(state, "description_analysis"):
            desc = state.description_analysis or {}
        if isinstance(desc, dict):
            arch_tags = [t for t in (desc.get("mechanism_tags") or [])
                         if any(a in t.lower() for a in ("x86", "aarch64", "riscv", "ppc", "arm", "arch"))]
            if arch_tags:
                consumption.architecture_selector = arch_tags[0].lower()
            else:
                # Check crash_type hints
                crash_type = str(getattr(state, "crash_type", "") or "").lower()
                for tag in ("x86", "aarch64", "riscv", "ppc", "arm"):
                    if tag in crash_type:
                        consumption.architecture_selector = tag
                        break

    # Populate state.harness_protocols from consumption model
    _populate_harness_protocols(state, consumption)


def _populate_harness_protocols(state: CyberGymState, consumption: Any) -> None:
    """Build a HarnessProtocol dict from the consumption model and add to state."""
    import hashlib as _hashlib

    scope = str(getattr(consumption, "endpoint_scope", "") or "")
    selector = str(getattr(consumption, "selector_expression", "") or "")
    magic = str(getattr(consumption, "magic_bytes", "") or "")
    patterns = list(getattr(consumption, "patterns", []) or [])
    carrier_stack = list(getattr(consumption, "carrier_stack", []) or [])
    required_wrappers = list(getattr(consumption, "required_wrappers", []) or [])

    # Only create a protocol entry if we have meaningful information
    if not scope and not selector and not magic and not carrier_stack:
        return

    existing = list(getattr(state, "harness_protocols", []) or [])
    # Check if we already have a protocol for this scope
    for proto in existing:
        if proto.get("endpoint_scope") == scope and proto.get("selector_expression") == selector:
            return

    # Build protocol entry
    material = f"{scope}|{selector}|{magic}|{'|'.join(carrier_stack)}"
    pid = f"hp_{_hashlib.blake2s(material.encode(), digest_size=6).hexdigest()}"

    # Build input contract from scope and patterns
    input_contract = scope or "unknown"
    if patterns:
        input_contract += f" ({', '.join(patterns[:3])})"

    # Build selector fields
    selector_fields = []
    if selector:
        selector_fields.append({
            "field": selector,
            "meaning": "dispatch selector",
            "encoding": "unknown",
        })

    # Build record delimiters from magic bytes and container info
    record_delimiters = []
    if magic:
        record_delimiters.append(magic)
    fmt = getattr(state, "input_format", None)
    if fmt:
        container = str(getattr(fmt, "container_structure", "") or "")
        if container:
            record_delimiters.append(container)

    protocol = {
        "protocol_id": pid,
        "endpoint_scope": scope,
        "input_contract": input_contract,
        "selector_expression": selector,
        "selector_fields": selector_fields,
        "record_delimiters": record_delimiters,
        "carrier_stack": carrier_stack,
        "required_wrappers": required_wrappers,
    }

    existing.append(protocol)
    state.harness_protocols = existing[:5]

    from ..core.runtime_context_contract import bump_context_revision
    bump_context_revision(state, "harness_protocols")


def _infer_endpoint_scope(pattern: str, patterns: list[str]) -> str:
    """Map consumption pattern(s) to an endpoint_scope label."""
    all_patterns = set(patterns + ([pattern] if pattern else []))

    if any(k in p for p in all_patterns for k in ("socket", "udp", "tcp", "network")):
        return "socket"
    if any(k in p for p in all_patterns for k in ("callback", "tasklet", "event")):
        return "callback"
    if any(k in p for p in all_patterns for k in ("apdu", "smartcard", "sc_transmit")):
        return "apdu"
    if any(k in p for p in all_patterns for k in ("multi_stage", "multi_record", "session")):
        return "multi_stage"
    if any(k in p for p in all_patterns for k in ("packet", "frame", "ieee")):
        return "packet"

    # Pattern-based inference from the single pattern field
    if pattern in ("direct_data_size", "magic_header", "struct_split"):
        return "buffer"
    if pattern in ("temp_file",):
        return "file"

    return ""
