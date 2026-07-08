"""Corpus intelligence — seed ranking by objective proximity.

Replaces the naive "first corpus file" default with scored seed selection
based on: harness acceptance, structural node presence, coverage proximity,
and protected-region rewrite cost.

This module is used by recipe.py's _best_seed_path to select the best
seed for a given objective and carrier contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import KnowledgePack

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedRecord:
    """Metadata about a single seed file for ranking purposes."""
    seed_id: str
    path: str
    digest: str
    detected_carrier: str = ""
    parse_status: str = "unknown"  # success, partial, failed, unknown
    structural_features: dict[str, Any] = field(default_factory=dict)
    harness_acceptance: str = "unknown"  # confirmed, likely, unknown, rejected
    coverage_fingerprint: str | None = None
    objective_proximity: float = 0.0
    size: int = 0
    provenance: str = ""
    source: str = "task_local"
    source_ref: str = ""
    license: str = "unknown"
    selection_scope: str = "runtime"
    runtime_allowed: bool = True
    reason: str = ""


@dataclass(frozen=True)
class SeedSelection:
    """Best seed selection with provenance and ranking reason."""

    record: SeedRecord
    reason: str
    ranked_count: int
    candidates_considered: int


class SeedSelector:
    """Rank seeds by objective proximity and structural fitness.

    Scoring:
      score = harness_acceptance_weight
            + target/frontier coverage proximity
            + required structural node presence
            + minimal protected-region rewrite cost
            + diversity bonus
            - parse ambiguity
            - size/runtime cost
    """

    # Weights for scoring components
    _W_HARNESS_ACCEPTED = 3.0
    _W_HARNESS_LIKELY = 1.5
    _W_PARSE_SUCCESS = 1.0
    _W_PARSE_PARTIAL = 0.5
    _W_CARRIER_MATCH = 2.0
    _W_SMALL_SIZE = 0.5      # bonus for small seeds (< 10KB)
    _W_STRUCTURAL_NODES = 1.0  # bonus per matching structural node
    _P_SIZE_PENALTY = -0.001  # per KB penalty for very large seeds
    _P_PARSE_FAILED = -1.0
    _P_HARNESS_REJECTED = -2.0

    def rank_seeds(
        self,
        seeds: list[SeedRecord],
        objective: Any | None = None,
        pack: Any | None = None,
        include_non_runtime: bool = False,
    ) -> list[SeedRecord]:
        """Rank seeds by score descending.

        Returns a new list sorted best-first.
        """
        if not seeds:
            return []

        candidates = [
            seed for seed in seeds
            if include_non_runtime or (seed.runtime_allowed and seed.selection_scope in {"runtime", "runtime_fallback"})
        ]
        if not candidates:
            return []

        # Determine target carrier from pack
        target_carriers: set[str] = set()
        if pack is not None and hasattr(pack, "descriptor"):
            target_carriers = set(pack.descriptor.carrier_families)

        # Score each seed
        scored: list[tuple[float, SeedRecord]] = []
        for seed in candidates:
            score = self._compute_score(seed, target_carriers, objective)
            scored.append((score, seed))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])
        return [seed for _, seed in scored]

    def _compute_score(
        self,
        seed: SeedRecord,
        target_carriers: set[str],
        objective: Any | None,
    ) -> float:
        score = 0.0

        # Harness acceptance
        if seed.harness_acceptance == "confirmed":
            score += self._W_HARNESS_ACCEPTED
        elif seed.harness_acceptance == "likely":
            score += self._W_HARNESS_LIKELY
        elif seed.harness_acceptance == "rejected":
            score += self._P_HARNESS_REJECTED

        # Parse status
        if seed.parse_status == "success":
            score += self._W_PARSE_SUCCESS
        elif seed.parse_status == "partial":
            score += self._W_PARSE_PARTIAL
        elif seed.parse_status == "failed":
            score += self._P_PARSE_FAILED

        # Carrier match
        if target_carriers and seed.detected_carrier in target_carriers:
            score += self._W_CARRIER_MATCH

        # Provenance policy: task-local first, pack fixture only as fallback.
        if seed.source == "task_local":
            score += 5.0
        elif seed.source == "pack_fixture":
            score += 0.25
        elif seed.source == "ground_truth_eval_only":
            score -= 100.0

        # Size — prefer small seeds
        size_kb = seed.size / 1024
        if size_kb < 10:
            score += self._W_SMALL_SIZE
        elif size_kb > 1000:
            score += self._P_SIZE_PENALTY * size_kb

        # Structural node presence (from features dict)
        if objective and isinstance(objective, dict):
            target_nodes = set(
                str(objective.get("target_function", "")).split(".")[:1]
            )
            seed_nodes = set(seed.structural_features.get("nodes", []))
            overlap = target_nodes & seed_nodes
            score += len(overlap) * self._W_STRUCTURAL_NODES

        # Objective proximity (pre-computed)
        score += seed.objective_proximity

        return score


def build_seed_records(
    corpus_files: list[str],
    pack: Any | None = None,
) -> list[SeedRecord]:
    """Build SeedRecords from a list of corpus file paths.

    Reads each file, computes digest, detects carrier format,
    and optionally parses with the knowledge pack.
    """
    from .evidence import _read_corpus_magics

    records: list[SeedRecord] = []

    # Detect magics for all files
    magics_map: dict[str, str] = {}
    for path in corpus_files:
        try:
            with open(path, "rb") as f:
                header = f.read(8)
            for sig, fmt_name in _MAGIC_SIGNATURES:
                if header.startswith(sig):
                    magics_map[path] = fmt_name
                    break
        except (OSError, IOError):
            pass

    for i, path in enumerate(corpus_files):
        try:
            size = os.path.getsize(path)
            # Compute digest
            with open(path, "rb") as f:
                digest = hashlib.blake2s(f.read(), digest_size=8).hexdigest()
        except (OSError, IOError):
            continue

        carrier = magics_map.get(path, "")
        parse_status = "unknown"
        structural_features: dict[str, Any] = {}
        source = "ground_truth_eval_only" if "cybergym_full_tasks" in str(path) else "task_local"
        selection_scope = "eval_only" if source == "ground_truth_eval_only" else "runtime"
        runtime_allowed = source == "task_local"
        reason = (
            "ground-truth PoC path is eval-only and blocked from runtime selection"
            if source == "ground_truth_eval_only"
            else "task-local corpus seed"
        )

        # If pack is available, try to parse
        if pack is not None and carrier and runtime_allowed:
            try:
                with open(path, "rb") as f:
                    artifact = f.read()
                result = pack.parse(artifact)
                parse_status = result.status
                structural_features = {
                    "nodes": list(result.field_map.keys())[:20],
                    "node_count": result.node_count,
                    "carrier_family": result.carrier_family,
                }
            except Exception:
                pass

        records.append(SeedRecord(
            seed_id=f"seed_{i}",
            path=path,
            digest=digest,
            detected_carrier=carrier,
            parse_status=parse_status,
            structural_features=structural_features,
            size=size,
            provenance=source,
            source=source,
            source_ref=path,
            selection_scope=selection_scope,
            runtime_allowed=runtime_allowed,
            reason=reason,
        ))

    return records


def load_pack_seed_index(pack_id: str) -> list[SeedRecord]:
    """Load curated pack-local seed metadata from corpus/index.jsonl."""
    safe_id = "".join(ch for ch in str(pack_id or "") if ch.isalnum() or ch in {"_", "-"})
    if not safe_id:
        return []
    try:
        corpus_dir = resources.files("cybergym_agent.agent_impl.knowledge.packs").joinpath(safe_id, "corpus")
        index_text = corpus_dir.joinpath("index.jsonl").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError, ValueError):
        return []

    records: list[SeedRecord] = []
    for idx, line in enumerate(index_text.splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        source = str(row.get("source") or "pack_fixture")
        source_ref = str(row.get("source_ref") or "")
        raw_path = str(row.get("path") or "")
        selection_scope = str(row.get("selection_scope") or "test_only")
        runtime_allowed = bool(row.get("runtime_allowed", False))
        if "cybergym_full_tasks" in source_ref or source == "ground_truth_eval_only":
            runtime_allowed = False
            selection_scope = "eval_only"
            source = "ground_truth_eval_only"

        resolved = corpus_dir.joinpath(raw_path)
        path = str(resolved)
        size = int(row.get("size") or 0)
        digest = str(row.get("sha256") or "")
        try:
            data = Path(path).read_bytes()
            size = len(data)
            digest = hashlib.sha256(data).hexdigest()
        except OSError:
            runtime_allowed = False

        records.append(SeedRecord(
            seed_id=str(row.get("seed_id") or f"{safe_id}_fixture_{idx}"),
            path=path,
            digest=digest,
            detected_carrier=str(row.get("format") or safe_id),
            parse_status=str(row.get("parse_status") or "unknown"),
            structural_features={"features": list(row.get("structural_features") or [])},
            harness_acceptance=str(row.get("harness_acceptance") or "unknown"),
            size=size,
            provenance=source,
            source=source,
            source_ref=source_ref,
            license=str(row.get("license") or "unknown"),
            selection_scope=selection_scope,
            runtime_allowed=runtime_allowed,
            reason="pack-local curated fixture fallback",
        ))
    return records


def select_seed_for_pack(
    corpus_files: list[str],
    pack: Any | None = None,
    objective: Any | None = None,
    allow_pack_fallback: bool = True,
) -> SeedSelection | None:
    """Select a runtime seed using task-local-first ranking and provenance guardrails."""
    task_records = build_seed_records(corpus_files, pack=pack)
    runtime_task_records = [
        record for record in task_records
        if record.runtime_allowed and record.source == "task_local"
    ]

    candidates = list(runtime_task_records)
    used_pack_fallback = False
    if not candidates and allow_pack_fallback and pack is not None and hasattr(pack, "descriptor"):
        pack_records = load_pack_seed_index(pack.descriptor.pack_id)
        candidates.extend([
            record for record in pack_records
            if record.runtime_allowed and record.selection_scope == "runtime_fallback"
        ])
        used_pack_fallback = bool(candidates)

    if not candidates:
        return None

    selector = SeedSelector()
    ranked = selector.rank_seeds(candidates, objective=objective, pack=pack)
    if not ranked:
        return None

    best = ranked[0]
    reason = (
        f"selected {best.seed_id} from {best.source}; "
        f"carrier={best.detected_carrier or 'unknown'} parse={best.parse_status}; "
        f"{'pack fixture fallback because no task-local runtime seed was available' if used_pack_fallback else 'task-local seed preferred'}"
    )
    return SeedSelection(
        record=best,
        reason=reason,
        ranked_count=len(ranked),
        candidates_considered=len(candidates),
    )


# Magic signatures (duplicated from evidence.py for standalone use)
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
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
