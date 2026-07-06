"""PoC recipe compiler — builds a structured recipe from active objectives,
input mappings, transcript plans, and rewrite plans.

The recipe is the bridge between "what the agent knows about the vulnerability"
and "what the agent should do to produce a PoC".  It is a compact dict stored
in state.metadata["poc_recipe"] that the observation layer renders into
Required Conditions and Next Action.
"""

from __future__ import annotations

import hashlib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState


TEMPLATE_FORMATS: frozenset[str] = frozenset({"tiff", "wav", "pdf", "elf", "zip", "packet", "tlv"})


def empty_poc_recipe() -> dict[str, Any]:
    """Return an empty recipe skeleton."""
    return {
        "recipe_id": "",
        "carrier": {
            "format": "",
            "seed_path": "",
            "container_stack": [],
            "endpoint_scope": "",
        },
        "objectives": [],
        "transcript": {
            "transcript_id": "",
            "steps": [],
        },
        "rewrite": {
            "rewrite_id": "",
            "operations": [],
            "invariants": [],
        },
        "trigger_mutations": [],
        "open_gaps": [],
        "sanity_expectations": [],
    }


def compile_poc_recipe(state: CyberGymState) -> dict[str, Any]:
    """Compile active objective/transcript/rewrite state into a compact recipe.

    The recipe is written to state.metadata["poc_recipe"] and the
    poc_recipe revision is bumped.
    """
    objective = _select_objective(state)
    rewrite = _select_rewrite(state, objective)
    transcript = _select_transcript(state, objective)
    mappings = _select_mappings(state, objective)
    domain_packs = _select_domain_pack_results(state)

    recipe = empty_poc_recipe()
    recipe["recipe_id"] = _stable_recipe_id(objective, rewrite, transcript, mappings)

    # Carrier info from input_format
    fmt = getattr(state, "input_format", None)
    consumption = getattr(fmt, "consumption", None) if fmt else None
    recipe["carrier"] = {
        "format": str(getattr(fmt, "format_type", "") or "") if fmt else "",
        "seed_path": _best_seed_path(state),
        "container_stack": list(getattr(consumption, "carrier_stack", []) or []) if consumption else [],
        "endpoint_scope": str(getattr(consumption, "endpoint_scope", "") or "") if consumption else "",
    }

    # Objectives
    if objective:
        recipe["objectives"] = [{
            "objective_id": objective.get("objective_id", ""),
            "kind": objective.get("kind", ""),
            "target_function": objective.get("target_function", ""),
            "observable": objective.get("observable", ""),
        }]

    # Transcript
    if transcript:
        recipe["transcript"] = {
            "transcript_id": transcript.get("transcript_id", ""),
            "steps": transcript.get("steps", []),
        }

    # Rewrite
    if rewrite:
        recipe["rewrite"] = {
            "rewrite_id": rewrite.get("rewrite_id", ""),
            "operations": rewrite.get("operations", []),
            "invariants": rewrite.get("invariants", []),
        }

    # Domain packs can fill carrier/rewrite defaults before gaps are computed.
    recipe["domain_packs"] = domain_packs
    _apply_domain_pack_patch(recipe, domain_packs)

    # Trigger mutations from input mappings
    recipe["trigger_mutations"] = _compile_mutations(mappings, objective)

    # Constraint solver turns extracted formulas into concrete byte writes when
    # offsets are known; otherwise it leaves field-localization gaps.
    constraint_report = _compile_constraint_solutions(state, mappings)
    recipe["constraint_solutions"] = constraint_report.get("solutions", [])
    recipe["trigger_mutations"].extend(constraint_report.get("mutations", []))

    # Open gaps
    recipe["open_gaps"] = _recipe_gaps(recipe, mappings)
    recipe["open_gaps"].extend(constraint_report.get("open_gaps", []))
    recipe["open_gaps"] = list(dict.fromkeys(str(g) for g in recipe["open_gaps"]))[:8]

    # Sanity expectations
    recipe["sanity_expectations"] = _compile_sanity_expectations(fmt, rewrite)
    for pack in domain_packs:
        recipe["sanity_expectations"].extend(list(pack.get("sanity_expectations") or [])[:3])
    recipe["sanity_expectations"] = recipe["sanity_expectations"][:8]

    # Write to state
    state.metadata["poc_recipe"] = recipe

    from ..core.runtime_context_contract import bump_context_revision
    bump_context_revision(state, "poc_recipe")

    return recipe


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _select_objective(state: CyberGymState) -> dict[str, Any]:
    """Select the primary active trigger objective."""
    objectives = list(getattr(state, "active_trigger_objectives", []) or [])
    for obj in objectives:
        if obj.get("status") == "active":
            return obj
    return objectives[0] if objectives else {}


def _select_rewrite(state: CyberGymState, objective: dict[str, Any]) -> dict[str, Any]:
    """Select a rewrite plan matching the objective."""
    rewrites = list(getattr(state, "structured_rewrite_plans", []) or [])
    obj_id = objective.get("objective_id", "")

    # Match by objective_id reference
    for rw in rewrites:
        if rw.get("objective_id") == obj_id:
            return rw
    # Fall back to first active rewrite
    for rw in rewrites:
        if rw.get("status") == "active":
            return rw
    return rewrites[0] if rewrites else {}


def _select_transcript(state: CyberGymState, objective: dict[str, Any]) -> dict[str, Any]:
    """Select a transcript plan matching the objective."""
    transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
    obj_id = objective.get("objective_id", "")

    for tr in transcripts:
        if tr.get("objective_id") == obj_id:
            return tr
    for tr in transcripts:
        if tr.get("status") in ("active", "candidate"):
            return tr
    return transcripts[0] if transcripts else {}


def _select_mappings(state: CyberGymState, objective: dict[str, Any]) -> list[dict[str, Any]]:
    """Select input mappings for the objective's path."""
    mappings = list(getattr(state, "active_input_mappings", []) or [])
    obj_path_id = objective.get("ranked_path_id", "")

    if obj_path_id:
        path_mappings = [m for m in mappings if m.get("ranked_path_id") == obj_path_id]
        if path_mappings:
            return path_mappings

    return mappings


def _best_seed_path(state: CyberGymState) -> str:
    """Find the best seed path from corpus or recipe.

    Uses SeedSelector for ranked selection when a knowledge pack is
    confirmed for the format.  Falls back to first corpus file otherwise.
    """
    # Check existing recipe first
    existing = state.get_poc_recipe()
    if isinstance(existing, dict):
        carrier = existing.get("carrier", {})
        if isinstance(carrier, dict) and carrier.get("seed_path"):
            return carrier["seed_path"]

    # Check corpus files
    corpus = list(getattr(state, "corpus_files", []) or [])

    # Try ranked seed selection via SeedSelector
    if corpus and len(corpus) > 1:
        try:
            from ..knowledge.corpus import SeedSelector, build_seed_records
            from ..knowledge.registry import get_knowledge_registry
            from ..knowledge.evidence import build_evidence_view

            registry = get_knowledge_registry()
            evidence = build_evidence_view(state)
            selected = registry.select_packs(evidence)

            pack = selected[0][0] if selected else None
            records = build_seed_records(corpus, pack=pack)
            if records:
                selector = SeedSelector()
                ranked = selector.rank_seeds(records, objective=None, pack=pack)
                if ranked:
                    return ranked[0].path
        except Exception:
            pass  # SeedSelector is supplementary — fallback is fine

    # Fallback: first corpus file
    if corpus:
        return corpus[0]

    # Check input_format sample paths
    fmt = getattr(state, "input_format", None)
    if fmt:
        samples = list(getattr(fmt, "sample_paths", []) or [])
        if samples:
            return samples[0]

    return ""


def _stable_recipe_id(
    objective: dict[str, Any],
    rewrite: dict[str, Any],
    transcript: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> str:
    """Generate a stable recipe ID from its components."""
    parts = [
        objective.get("objective_id", ""),
        rewrite.get("rewrite_id", ""),
        transcript.get("transcript_id", ""),
        str(len(mappings)),
    ]
    material = "|".join(parts)
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"rec_{h}"


def _compile_mutations(
    mappings: list[dict[str, Any]],
    objective: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert input mappings + objective into trigger mutation actions."""
    mutations: list[dict[str, Any]] = []

    # Strategy mapping: argument_role + value_strategy -> action description
    _STRATEGY_DESCRIPTIONS = {
        ("length", "oversize"): "set declared length > actual allocation/available bytes",
        ("index", "negative"): "encode signed negative or wraparound index",
        ("offset", "wrap"): "set offset near UINT_MAX or before base",
        ("selector", "choose_case"): "set chunk/type/arch selector",
        ("state", "duplicate_free_sequence"): "emit sequence that frees then reuses state",
        ("pointer", "null_or_stale"): "encode absent pointer/ref after state creation",
    }

    for m in mappings:
        role = str(m.get("argument_role", "") or "")
        strategy = str(m.get("value_strategy", "") or "")
        mapping_id = str(m.get("mapping_id", "") or "")
        offset = m.get("offset")
        width = m.get("width")

        desc = _STRATEGY_DESCRIPTIONS.get((role, strategy), f"{role}={strategy}")

        mutation: dict[str, Any] = {
            "mapping_id": mapping_id,
            "argument_role": role,
            "value_strategy": strategy,
            "action": desc,
            "offset": offset,
            "width": width,
        }

        # If offset is known, mark as executable
        if offset is not None and width is not None:
            mutation["executable"] = True
            if "endian" in m:
                mutation["endian"] = m.get("endian")
        else:
            mutation["executable"] = False

        mutations.append(mutation)

    return mutations[:6]


def _select_domain_pack_results(state: CyberGymState) -> list[dict[str, Any]]:
    try:
        from ..knowledge import get_knowledge_registry, build_evidence_view
        from ..knowledge.models import DetectionResult
        from ..core.runtime_context_contract import bump_context_revision

        registry = get_knowledge_registry()
        if registry.is_empty():
            return []

        evidence = build_evidence_view(state)
        selected = registry.select_packs(evidence)

        if not selected:
            return []

        # Convert to legacy dict format for backward compatibility
        packs: list[dict[str, Any]] = []
        for pack, det_result in selected:
            pack_dict: dict[str, Any] = {
                "pack": pack.descriptor.pack_id,
                "match_score": round(det_result.score, 3),
                "status": "ready" if det_result.decision == "confirmed" else "partial",
                "recipe_patch": {"carrier": {"format": pack.descriptor.carrier_families[0] if pack.descriptor.carrier_families else "", "seed_policy": "minimal_template_ok"}},
                "rewrite_plan": {"operations": [], "invariants": [f"preserve_{pack.descriptor.pack_id}_carrier"]},
                "open_gaps": list(det_result.missing_evidence),
                "sanity_expectations": [{"kind": "format", "expected": pack.descriptor.pack_id, "description": f"preserve {pack.descriptor.pack_id} carrier"}],
            }
            packs.append(pack_dict)

        if packs:
            state.metadata["domain_packs"] = packs
            bump_context_revision(state, "domain_packs")
        return packs
    except Exception:
        return []


def _apply_domain_pack_patch(recipe: dict[str, Any], packs: list[dict[str, Any]]) -> None:
    ready_or_partial = [p for p in packs if p.get("status") in {"ready", "partial"}]
    if not ready_or_partial:
        return
    selected = ready_or_partial[0]
    patch = selected.get("recipe_patch", {}) or {}
    carrier_patch = patch.get("carrier", {}) if isinstance(patch, dict) else {}
    if isinstance(carrier_patch, dict):
        carrier = recipe.setdefault("carrier", {})
        for key, value in carrier_patch.items():
            if value and not carrier.get(key):
                carrier[key] = value
    rewrite_plan = selected.get("rewrite_plan", {}) or {}
    if rewrite_plan:
        rewrite = recipe.setdefault("rewrite", {"rewrite_id": "", "operations": [], "invariants": []})
        if not rewrite.get("rewrite_id"):
            rewrite["rewrite_id"] = f"rw_pack_{selected.get('pack', 'domain')}"
        rewrite["operations"] = list(rewrite.get("operations") or []) + list(rewrite_plan.get("operations") or [])
        rewrite["invariants"] = list(dict.fromkeys(
            list(rewrite.get("invariants") or []) + list(rewrite_plan.get("invariants") or [])
        ))
    for gap in list(selected.get("open_gaps") or []):
        recipe.setdefault("open_gaps", []).append(str(gap))


def _compile_constraint_solutions(
    state: CyberGymState,
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    constraints = list((getattr(state, "metadata", {}) or {}).get("numeric_constraints", []) or [])
    if not constraints:
        return {"solutions": [], "mutations": [], "open_gaps": []}
    try:
        from ...analysis.constraint_solver import solve_constraints
        from ..core.byte_layout_solver import compile_solution_to_mutations
        from ..core.runtime_context_contract import bump_context_revision

        solutions = solve_constraints(constraints)
        mutations: list[dict[str, Any]] = []
        open_gaps: list[str] = []
        for solution in solutions[:6]:
            compiled = compile_solution_to_mutations(solution, mappings)
            mutations.extend(compiled.get("mutations", []))
            open_gaps.extend(compiled.get("open_gaps", []))
        state.metadata["constraint_solutions"] = solutions[:8]
        bump_context_revision(state, "constraint_solutions")
        return {"solutions": solutions[:8], "mutations": mutations[:8], "open_gaps": open_gaps[:8]}
    except Exception:
        return {"solutions": [], "mutations": [], "open_gaps": ["constraint_solver_failed: unable to compile numeric constraints"]}


def _recipe_gaps(
    recipe: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> list[str]:
    """Compute open gaps in the recipe."""
    gaps: list[str] = []

    # Carrier gaps
    carrier = recipe.get("carrier", {})
    if not carrier.get("format"):
        gaps.append("needs_format: input format not determined")
    carrier_format = str(carrier.get("format") or "").lower()
    if not carrier.get("seed_path") and carrier.get("format"):
        if carrier_format in TEMPLATE_FORMATS:
            carrier["seed_policy"] = "minimal_template_ok"
        else:
            gaps.append("needs_seed: no seed file available for carrier format")

    # Unresolved mappings
    for m in mappings:
        mid = m.get("mapping_id", "")
        role = m.get("argument_role", "")
        status = m.get("status", "")
        if status == "needs_field_localization":
            gaps.append(f"needs_field_localization: mapping {mid} controls {role} but offset is unknown")

    # Transcript gaps
    transcript = recipe.get("transcript", {})
    if transcript.get("transcript_id") and not transcript.get("steps"):
        gaps.append(f"needs_transcript_steps: transcript {transcript['transcript_id']} has no steps")

    # Rewrite gaps
    rewrite = recipe.get("rewrite", {})
    if rewrite.get("rewrite_id") and not rewrite.get("operations"):
        gaps.append(f"needs_rewrite_ops: rewrite {rewrite['rewrite_id']} has no operations")

    for pack in list(recipe.get("domain_packs") or []):
        if pack.get("status") == "partial":
            for gap in list(pack.get("open_gaps") or [])[:2]:
                gaps.append(str(gap))
        elif pack.get("status") == "blocked":
            gaps.append(f"domain_pack_blocked: {pack.get('pack', '')}")

    # Checksum invariant without recompute
    invariants = rewrite.get("invariants", [])
    has_checksum = any("checksum" in str(inv).lower() for inv in invariants)
    ops = rewrite.get("operations", [])
    has_checksum_op = any("checksum" in str(op.get("kind", "")).lower() for op in ops)
    if has_checksum and not has_checksum_op:
        gaps.append("needs_checksum_recompute: rewrite declares checksum invariant but no checksum operation")

    return gaps[:8]


def _compile_sanity_expectations(
    fmt: Any,
    rewrite: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compile sanity expectations from format and rewrite plan."""
    expectations: list[dict[str, Any]] = []

    if fmt:
        magic = str(getattr(fmt, "magic_bytes", "") or "")
        fmt_type = str(getattr(fmt, "format_type", "") or "")
        container = str(getattr(fmt, "container_structure", "") or "")

        if magic:
            expectations.append({
                "kind": "magic",
                "expected": magic,
                "description": f"PoC must start with magic bytes {magic}",
            })
        if fmt_type:
            expectations.append({
                "kind": "format",
                "expected": fmt_type,
                "description": f"PoC must be valid {fmt_type} format",
            })
        if container:
            expectations.append({
                "kind": "container_stack",
                "expected": container,
                "description": f"PoC must preserve container structure: {container}",
            })

    # Rewrite invariants become sanity expectations
    for inv in rewrite.get("invariants", []):
        inv_str = str(inv).lower()
        if "checksum" in inv_str:
            expectations.append({
                "kind": "checksum",
                "expected": "recomputed",
                "description": f"Rewrite invariant: {inv}",
            })
        elif "length" in inv_str or "size" in inv_str:
            expectations.append({
                "kind": "length_table",
                "expected": "consistent",
                "description": f"Rewrite invariant: {inv}",
            })

    return expectations[:6]
