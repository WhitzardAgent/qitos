"""Capability matrix — per-task coverage assessment across all knowledge packs.

This is an offline analysis tool, not a runtime component.  It reads
tasks from tasks.json, runs each task through detection, and outputs
a structured coverage assessment per task.

Used to:
1. Identify which tasks are covered by which packs
2. Track coverage_level (assisted/diagnosable/buildable/end_to_end)
3. Find blocking_capabilities for uncovered tasks
4. Validate that the knowledge system meets coverage targets
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence import EvidenceView, build_evidence_view
from .models import DetectionResult, PackDescriptor
from .registry import get_knowledge_registry
from .backend_registry import get_backend_registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskCapability:
    """Per-task coverage assessment."""
    task_id: str
    project_name: str = ""
    description_snippet: str = ""
    carrier_contract: str = "unknown"   # confirmed, candidate, unknown
    harness_contract: str = "unknown"   # confirmed, partial, unknown
    seed_strategy: str = "missing"      # ranked, available, missing
    parser_backend: str = "missing"     # available, partial, missing
    builder_backend: str = "missing"    # available, partial, missing
    validator_backend: str = "missing"  # available, partial, missing
    oracle_predicate: str = "unknown"   # confirmed, partial, unknown
    runtime_probe: str = "not_applicable"  # available, unavailable, not_applicable
    stateful_transcript: str = "not_applicable"  # available, needed_missing, not_applicable
    coverage_level: str = "assisted"    # assisted, diagnosable, buildable, end_to_end
    blocking_capabilities: tuple[str, ...] = ()
    matched_pack: str = ""
    detection_score: float = 0.0


def generate_capability_matrix(
    tasks: list[dict[str, Any]],
    registry: Any | None = None,
    backend: Any | None = None,
) -> list[TaskCapability]:
    """Generate a capability matrix for a list of tasks.

    Each task dict should have at minimum:
      - task_id or id
      - vulnerability_description (or description)
      - project_name (optional)
    """
    if registry is None:
        registry = get_knowledge_registry()
    if backend is None:
        backend = get_backend_registry()

    results: list[TaskCapability] = []

    for task in tasks:
        capability = _assess_task(task, registry, backend)
        results.append(capability)

    return results


def _assess_task(
    task: dict[str, Any],
    registry: Any,
    backend: Any,
) -> TaskCapability:
    """Assess coverage for a single task."""
    task_id = str(task.get("task_id") or task.get("id", ""))
    project_name = str(task.get("project_name", ""))
    description = str(task.get("vulnerability_description") or task.get("description", ""))
    description_snippet = description[:80]

    # Build evidence view from task data
    evidence = _task_to_evidence(task)

    # Run detection
    selected = registry.select_packs(evidence)
    matched_pack = ""
    detection_score = 0.0
    carrier_contract = "unknown"
    parser_backend = "missing"
    builder_backend = "missing"
    validator_backend = "missing"

    if selected:
        pack, det_result = selected[0]
        matched_pack = pack.descriptor.pack_id
        detection_score = det_result.score
        carrier_contract = det_result.decision if det_result.decision in ("confirmed", "candidate") else "unknown"

        # Check backends
        desc = pack.descriptor
        missing = backend.check_pack_requirements(desc.required_backends)
        if not missing:
            parser_backend = "available"
            builder_backend = "available"
            validator_backend = "available"
        elif len(missing) < len(desc.required_backends):
            parser_backend = "partial"

    # Determine harness contract
    harness_contract = "unknown"
    if evidence.harness_input_contract:
        harness_contract = "confirmed"
    elif evidence.harness_protocols:
        harness_contract = "partial"

    # Determine seed strategy
    seed_strategy = "missing"
    if evidence.corpus_files:
        seed_strategy = "available"
    if evidence.detected_magics:
        seed_strategy = "ranked"

    # Determine coverage level
    blocking: list[str] = []

    if carrier_contract in ("confirmed", "candidate") and parser_backend in ("available", "partial"):
        level = "diagnosable"
    elif carrier_contract in ("confirmed", "candidate"):
        level = "assisted"
        blocking.append("parser_backend")
    else:
        level = "assisted"
        if not matched_pack:
            blocking.append("no_matching_pack")

    if level == "diagnosable" and builder_backend == "available":
        level = "buildable"

    if level == "buildable" and validator_backend == "available" and harness_contract in ("confirmed", "partial"):
        level = "end_to_end"

    if builder_backend == "missing" and level in ("diagnosable", "assisted"):
        blocking.append("builder_backend")
    if validator_backend == "missing" and level in ("buildable", "diagnosable"):
        blocking.append("validator_backend")
    if harness_contract == "unknown" and level != "assisted":
        blocking.append("harness_contract")

    return TaskCapability(
        task_id=task_id,
        project_name=project_name,
        description_snippet=description_snippet,
        carrier_contract=carrier_contract,
        harness_contract=harness_contract,
        seed_strategy=seed_strategy,
        parser_backend=parser_backend,
        builder_backend=builder_backend,
        validator_backend=validator_backend,
        coverage_level=level,
        blocking_capabilities=tuple(blocking),
        matched_pack=matched_pack,
        detection_score=detection_score,
    )


def _task_to_evidence(task: dict[str, Any]) -> EvidenceView:
    """Convert a task dict to an EvidenceView for detection."""
    description = str(task.get("vulnerability_description") or task.get("description", ""))
    project_name = str(task.get("project_name", ""))
    task_id = str(task.get("task_id") or task.get("id", ""))
    crash_type = str(task.get("crash_type", ""))

    # Check for format signals in description
    detected_magics: tuple[str, ...] = ()
    if "pdf" in description.lower():
        detected_magics = ("pdf",)
    elif "tiff" in description.lower() or "tif" in description.lower():
        detected_magics = ("tiff",)
    elif "png" in description.lower():
        detected_magics = ("png",)
    elif "elf" in description.lower():
        detected_magics = ("elf",)
    elif "zip" in description.lower():
        detected_magics = ("zip",)

    return EvidenceView(
        task_id=task_id,
        project_name=project_name,
        vulnerability_description=description,
        crash_type=crash_type,
        detected_magics=detected_magics,
    )


def compute_coverage_summary(matrix: list[TaskCapability]) -> dict[str, Any]:
    """Compute aggregate coverage statistics from a capability matrix."""
    total = len(matrix)
    if total == 0:
        return {"total": 0}

    by_level: dict[str, int] = {}
    by_pack: dict[str, int] = {}
    by_blocker: dict[str, int] = {}

    for cap in matrix:
        by_level[cap.coverage_level] = by_level.get(cap.coverage_level, 0) + 1
        if cap.matched_pack:
            by_pack[cap.matched_pack] = by_pack.get(cap.matched_pack, 0) + 1
        for blocker in cap.blocking_capabilities:
            by_blocker[blocker] = by_blocker.get(blocker, 0) + 1

    return {
        "total": total,
        "by_level": by_level,
        "by_level_pct": {k: round(v / total * 100, 1) for k, v in by_level.items()},
        "by_pack": by_pack,
        "by_blocker": by_blocker,
        "confirmed_pct": round(
            sum(1 for c in matrix if c.carrier_contract == "confirmed") / total * 100, 1
        ),
        "diagnosable_pct": round(
            sum(1 for c in matrix if c.coverage_level in ("diagnosable", "buildable", "end_to_end")) / total * 100, 1
        ),
        "buildable_pct": round(
            sum(1 for c in matrix if c.coverage_level in ("buildable", "end_to_end")) / total * 100, 1
        ),
        "end_to_end_pct": round(
            sum(1 for c in matrix if c.coverage_level == "end_to_end") / total * 100, 1
        ),
    }
