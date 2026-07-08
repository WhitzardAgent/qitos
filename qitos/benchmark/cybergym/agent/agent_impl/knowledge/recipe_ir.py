"""Recipe IR — typed recipe plan operations, DAG ordering, and backpatch.

This module provides the intermediate representation layer between
the knowledge packs (which produce RecipePlans) and the structured
rewriter / pack builders (which apply them).

Key functions:
- topological_sort_ops: Order operations by read/write span dependencies
- detect_conflicts: Find overlapping write spans
- apply_backpatch: Fixed-point iteration for derived field recomputation
- recipe_to_dict: Backward-compatible conversion for legacy consumers
"""

from __future__ import annotations

import logging
from typing import Any

from .models import ExpectedEffect, Invariant, RecipeOperation, RecipePlan

logger = logging.getLogger(__name__)

_MAX_BACKPATCH_ITERATIONS = 3


def topological_sort_ops(ops: tuple[RecipeOperation, ...]) -> list[RecipeOperation]:
    """Sort operations in dependency order based on read/write span conflicts.

    Operations that write to a span that another operation reads from
    must come first.  Uses a simple topological sort on the conflict graph.
    """
    if len(ops) <= 1:
        return list(ops)

    # Build conflict graph: op A must precede op B if A writes a span
    # that B reads from
    n = len(ops)
    # precedes[i] = set of ops that must come before i
    precedes: list[set[int]] = [set() for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            # If op i writes a span that op j reads, i must precede j
            if _span_overlap(ops[i].write_spans, ops[j].read_spans):
                precedes[j].add(i)

    # Topological sort (Kahn's algorithm)
    in_degree = [len(p) for p in precedes]
    queue = [i for i in range(n) if in_degree[i] == 0]
    result: list[int] = []

    while queue:
        # Pick the one with lowest index for stability
        node = min(queue)
        queue.remove(node)
        result.append(node)

        for j in range(n):
            if node in precedes[j]:
                precedes[j].remove(node)
                in_degree[j] -= 1
                if in_degree[j] == 0:
                    queue.append(j)

    # If not all nodes are in result, there's a cycle — fall back to insertion order
    if len(result) < n:
        logger.warning("Cycle detected in operation dependencies, using insertion order")
        return list(ops)

    return [ops[i] for i in result]


def detect_conflicts(ops: tuple[RecipeOperation, ...]) -> list[dict[str, Any]]:
    """Detect overlapping write spans between operations.

    Returns a list of conflict reports, each with:
      op_a, op_b, overlapping_spans
    """
    conflicts: list[dict[str, Any]] = []

    for i in range(len(ops)):
        for j in range(i + 1, len(ops)):
            overlap = _span_overlap_detail(ops[i].write_spans, ops[j].write_spans)
            if overlap:
                conflicts.append({
                    "op_a": ops[i].op_id,
                    "op_b": ops[j].op_id,
                    "overlapping_spans": overlap,
                })

    return conflicts


def apply_backpatch(
    ops: tuple[RecipeOperation, ...],
    results_so_far: dict[str, Any],
) -> list[RecipeOperation]:
    """Apply fixed-point backpatch for derived field recomputation.

    After applying operations, derived fields (lengths, checksums, offsets)
    may need recomputation.  This function generates additional recompute
    operations based on invalidated_derivations from the applied ops.

    Returns a list of new recompute operations to apply.  If the caller
    needs convergence checking, it should call this function repeatedly
    until no new operations are produced (up to _MAX_BACKPATCH_ITERATIONS).
    """
    recompute_ops: list[RecipeOperation] = []
    derived_fields: set[str] = set()

    # Collect all invalidated derivations
    for op in ops:
        for derived in op.invalidated_derivations:
            # Wildcard patterns like "pdf.*.stream.length"
            if "*" in derived:
                # Find matching fields from results
                field_map = results_so_far.get("field_map", {})
                for field_name in field_map:
                    if _pattern_match(derived, field_name):
                        derived_fields.add(field_name)
            else:
                derived_fields.add(derived)

    # Generate recompute operations for each derived field
    for field_name in sorted(derived_fields):
        recompute_ops.append(RecipeOperation(
            op_id=f"recompute_{field_name}",
            kind="recompute",
            target_node_id=field_name,
            invalidated_derivations=(field_name,),
            rollback_hint=f"restore original value of {field_name}",
        ))

    return recompute_ops


def recipe_to_dict(plan: RecipePlan) -> dict[str, Any]:
    """Convert a RecipePlan dataclass to a backward-compatible dict.

    This produces the old dict format that existing consumers
    (recipe.py, candidate_builder.py, observations) expect.
    """
    return {
        "recipe_id": plan.recipe_id,
        "schema_version": plan.schema_version,
        "objective_id": plan.objective_id,
        "carrier": plan.carrier,
        "carrier_contract_id": plan.carrier_contract_id,
        "seed_id": plan.seed_id,
        "operations": [
            _operation_to_dict(op) for op in plan.operations
        ],
        "invariants": [
            {"invariant_id": inv.invariant_id, "kind": inv.kind,
             "expression": inv.expression, "protected": inv.protected}
            for inv in plan.invariants
        ],
        "expected_effects": [
            {"effect_id": eff.effect_id, "target_expression": eff.target_expression,
             "desired_relation": eff.desired_relation}
            for eff in plan.expected_effects
        ],
        "trigger_mutations": plan.trigger_mutations,
        "open_gaps": plan.open_gaps,
        "sanity_expectations": plan.sanity_expectations,
        "evidence_ids": list(plan.evidence_ids),
        "knowledge_revision": plan.knowledge_revision,
    }


def recipe_from_dict(value: dict[str, Any] | RecipePlan | None) -> RecipePlan | None:
    """Reconstruct a RecipePlan from the serializable metadata form."""
    if isinstance(value, RecipePlan):
        return value
    if not isinstance(value, dict):
        return None

    operations = tuple(
        RecipeOperation(
            op_id=str(item.get("op_id", "") or ""),
            kind=str(item.get("kind", "") or ""),
            target_node_id=item.get("target_node_id"),
            read_spans=tuple(tuple(span) for span in list(item.get("read_spans", []) or [])),
            write_spans=tuple(tuple(span) for span in list(item.get("write_spans", []) or [])),
            preconditions=tuple(str(x) for x in list(item.get("preconditions", []) or [])),
            postconditions=tuple(str(x) for x in list(item.get("postconditions", []) or [])),
            invalidated_derivations=tuple(str(x) for x in list(item.get("invalidated_derivations", []) or [])),
            rollback_hint=str(item.get("rollback_hint", "") or ""),
            evidence_id=str(item.get("evidence_id", "") or ""),
            ast_transform=dict(item.get("ast_transform", {}) or {}),
        )
        for item in list(value.get("operations", []) or [])
        if isinstance(item, dict)
    )
    invariants = tuple(
        Invariant(
            invariant_id=str(item.get("invariant_id", "") or ""),
            kind=str(item.get("kind", "") or ""),
            expression=str(item.get("expression", "") or ""),
            protected=bool(item.get("protected", False)),
        )
        for item in list(value.get("invariants", []) or [])
        if isinstance(item, dict)
    )
    expected_effects = tuple(
        ExpectedEffect(
            effect_id=str(item.get("effect_id", "") or ""),
            target_expression=str(item.get("target_expression", "") or ""),
            desired_relation=str(item.get("desired_relation", "") or ""),
            expected_runtime_probe=str(item.get("expected_runtime_probe", "") or ""),
        )
        for item in list(value.get("expected_effects", []) or [])
        if isinstance(item, dict)
    )

    return RecipePlan(
        recipe_id=str(value.get("recipe_id", "") or ""),
        schema_version=str(value.get("schema_version", "2.0") or "2.0"),
        objective_id=str(value.get("objective_id", "") or ""),
        carrier_contract_id=str(value.get("carrier_contract_id", "") or ""),
        seed_id=str(value.get("seed_id", "") or ""),
        operations=operations,
        invariants=invariants,
        expected_effects=expected_effects,
        evidence_ids=tuple(str(x) for x in list(value.get("evidence_ids", []) or [])),
        knowledge_revision=str(value.get("knowledge_revision", "") or ""),
        carrier=dict(value.get("carrier", {}) or {}),
        trigger_mutations=list(value.get("trigger_mutations", []) or []),
        open_gaps=list(value.get("open_gaps", []) or []),
        sanity_expectations=list(value.get("sanity_expectations", []) or []),
    )


def _operation_to_dict(op: RecipeOperation) -> dict[str, Any]:
    """Convert a RecipeOperation to a dict."""
    return {
        "op_id": op.op_id,
        "kind": op.kind,
        "target_node_id": op.target_node_id,
        "read_spans": list(op.read_spans),
        "write_spans": list(op.write_spans),
        "preconditions": list(op.preconditions),
        "postconditions": list(op.postconditions),
        "invalidated_derivations": list(op.invalidated_derivations),
        "rollback_hint": op.rollback_hint,
        "evidence_id": op.evidence_id,
        "ast_transform": op.ast_transform,
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _span_overlap(
    write_spans: tuple[tuple[int, int], ...],
    read_spans: tuple[tuple[int, int], ...],
) -> bool:
    """Check if any write span overlaps any read span."""
    for ws, we in write_spans:
        for rs, re_ in read_spans:
            if ws < re_ and we > rs:
                return True
    return False


def _span_overlap_detail(
    spans_a: tuple[tuple[int, int], ...],
    spans_b: tuple[tuple[int, int], ...],
) -> list[tuple[int, int]]:
    """Find overlapping spans between two sets of write spans."""
    overlaps: list[tuple[int, int]] = []
    for as_, ae in spans_a:
        for bs, be in spans_b:
            start = max(as_, bs)
            end = min(ae, be)
            if start < end:
                overlaps.append((start, end))
    return overlaps


def _pattern_match(pattern: str, field_name: str) -> bool:
    """Simple wildcard pattern matching.

    Only supports * as a single-level wildcard.
    E.g., "pdf.*.stream.length" matches "pdf.object.7.stream.length"
    """
    pattern_parts = pattern.split(".")
    name_parts = field_name.split(".")

    if len(pattern_parts) != len(name_parts):
        return False

    for pp, np in zip(pattern_parts, name_parts):
        if pp == "*":
            continue
        if pp != np:
            return False

    return True
