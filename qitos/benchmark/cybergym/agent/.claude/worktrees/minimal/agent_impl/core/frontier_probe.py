"""Runtime wrapper for reachability frontier probes."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def record_frontier_probe(
    state: Any,
    *,
    submit_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ...analysis.reachability_frontier import classify_reachability_frontier
    from .runtime_context_contract import bump_context_revision

    probe = classify_reachability_frontier(
        submit_result=submit_result,
        consistency_signals=list(getattr(state, "consistency_signals", []) or []),
        harness_protocols=list(getattr(state, "harness_protocols", []) or []),
        latest_sanity=(getattr(state, "metadata", {}) or {}).get("last_poc_sanity") or {},
        objectives=list(getattr(state, "active_trigger_objectives", []) or []),
    )
    material = json.dumps(probe, sort_keys=True, default=str).encode()
    probe["probe_id"] = "fp_" + hashlib.blake2s(material, digest_size=5).hexdigest()
    probes = list((getattr(state, "metadata", {}) or {}).get("frontier_probes", []) or [])
    probes.append(probe)
    state.metadata["frontier_probes"] = probes[-8:]
    bump_context_revision(state, "frontier_probes")
    return probe
