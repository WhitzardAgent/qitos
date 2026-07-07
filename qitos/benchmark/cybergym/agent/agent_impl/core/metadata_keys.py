"""Central metadata keys used by the CyberGym runtime.

New runtime code should import keys from this module instead of writing raw
string literals.  Legacy keys are kept here only as compatibility aliases.

Groups
------
prompt          – keys that feed into observation / prompt rendering
context_revision – context contract revision tracking
feedback        – submit-poc feedback and arbitration
candidate       – sink-candidate and family-queue metadata
analysis        – static-analysis bundle, service results, navigation
oracle          – oracle assessment results
harness         – harness detection and consumption model
poc             – PoC recipe, sanity, build results
frontier        – frontier-probe metadata
dynamic         – staged vulnerable binary and invocation profile
compat          – keys that exist only for backward-compatible migration
tui             – TUI / observation-sidecar metadata
internal        – runtime-internal bookkeeping (reduce round, phase latch, etc.)
"""

from __future__ import annotations

from typing import Any, MutableMapping


# ---------------------------------------------------------------------------
# Prompt / observation rendering
# ---------------------------------------------------------------------------

ONE_SHOT_REMINDER_RENDERED: str = "_one_shot_reminder_rendered"
"""Owner: observations — marks that the one-shot reminder was rendered."""

DESCRIPTION_ANALYSIS_DIRTY: str = "_description_analysis_dirty"
"""Owner: tracking_tools — set when description analysis changed state."""

DESCRIPTION_REFERENCE_GAPS: str = "_description_reference_gaps"
"""Owner: static_analysis_runtime — gaps found in description reference."""

DESCRIPTION_NAVIGATION_ERROR: str = "_description_navigation_error"
"""Owner: static_analysis_runtime — error from description navigation."""

DESCRIPTION_RANKED_PATH_ERROR: str = "_description_ranked_path_error"
"""Owner: static_analysis_runtime — error from ranked-path query."""

DESCRIPTION_REACHABILITY_ERROR: str = "_description_reachability_error"
"""Owner: static_analysis_runtime — error from reachability check."""

DESCRIPTION_ANALYSIS_REFRESH_ERROR: str = "_description_analysis_refresh_error"
"""Owner: static_analysis_runtime — error during description refresh."""

DESCRIPTION_VALIDATION_HINTS: str = "_desc_validation_hints"
"""Owner: static_analysis_runtime — hints from description validation."""

REPLAN_HINT: str = "_replan_hint"
"""Owner: validation — reason string when a replan is suggested."""

CALLLEE_GATE_HINTS: str = "_callee_gate_hints"
"""Owner: agent reduce — gate hints from callee analysis."""

AUTO_DEEPEN_HINTS: str = "_auto_deepen_hints"
"""Owner: static_analysis_runtime / observations — auto-deepen hint queue."""

AUTO_DEEPEN_HINT_AGES: str = "_auto_deepen_hint_ages"
"""Owner: observations — age tracking for auto-deepen hints."""

OBS_LAST_STEP: str = "_obs_last_step"
"""Owner: observations — last step rendered for delta comparison."""

OBS_LAST_PHASE: str = "_obs_last_phase"
"""Owner: observations — last phase rendered for delta comparison."""

OBS_LAST_EVENTS: str = "_obs_last_events"
"""Owner: observations — last semantic event snapshot for delta comparison."""

OBS_LAST_REVISIONS: str = "_obs_last_revisions"
"""Owner: observations — last context revision map for delta comparison."""

OBS_LAST_SECTIONS: str = "_obs_last_sections"
"""Owner: observations — last section content hashes for delta comparison."""

# ---------------------------------------------------------------------------
# Context revision protocol
# ---------------------------------------------------------------------------

CONTEXT_REVISIONS: str = "_context_revisions"
"""Owner: runtime_context_contract — canonical context revision map."""

LEGACY_CONTEXT_REVISIONS: str = "_vnext_context_revisions"
# Data key from pre-refactor state files; kept for read-compat only.
# IMPORTANT: This key exists solely for backward-compatible migration of
# old state snapshots.  No new code should write to this key — all writes
# must go through CONTEXT_REVISIONS instead.
"""Legacy key — read as fallback only; no new writes."""

# ---------------------------------------------------------------------------
# Feedback / arbitration
# ---------------------------------------------------------------------------

LAST_FEEDBACK_EFFECT: str = "last_feedback_effect"
"""Owner: feedback — effect classification from last submit feedback."""

LAST_FEEDBACK_ACTION: str = "last_feedback_action"
"""Owner: feedback_arbitration — chosen action from feedback arbitration."""

LAST_FEEDBACK_ACTION_RESULT: str = "last_feedback_action_result"
"""Owner: feedback_action_runner — result of running the chosen action."""

LAST_POC_SANITY: str = "last_poc_sanity"
"""Owner: feedback — PoC sanity-check result."""

POC_SANITY_WARNINGS: str = "poc_sanity_warnings"
"""Owner: feedback — accumulated sanity warnings."""

RECENT_NOTES: str = "_recent_notes"
"""Owner: feedback — recent note entries."""

# ---------------------------------------------------------------------------
# Candidate / sink
# ---------------------------------------------------------------------------

PENDING_SINK_ANALYSIS: str = "_pending_sink_analysis"
"""Owner: tracking_tools / agent — candidate id awaiting sink analysis."""

ENTRY_POINT_SINK_RECORDED: str = "entry_point_sink_recorded"
"""Owner: tracking_tools — marks that entry-point sink was recorded."""

MANUAL_PHASE_SWITCH: str = "_manual_phase_switch"
"""Owner: tracking_tools — target phase for manual switch."""

CANDIDATE_SET_INCOMPLETE_REASON: str = "candidate_set_incomplete_reason"
"""Owner: static_analysis_runtime — reasons the candidate set is incomplete."""

# ---------------------------------------------------------------------------
# Analysis / bundle
# ---------------------------------------------------------------------------

CALL_PATH_EVIDENCE: str = "call_path_evidence"
"""Owner: analysis_bundle_runtime — call-path evidence from bundle sync."""

NUMERIC_CONSTRAINTS: str = "numeric_constraints"
"""Owner: analysis_bundle_runtime — numeric constraints from bundle sync."""

API_REACHABILITY: str = "api_reachability"
"""Owner: analysis_bundle_runtime — API reachability info."""

STATIC_INDEX_SUMMARY: str = "_static_index_summary"
"""Owner: agent reduce — summary of static index build."""

ANALYSIS_BRIEF_SECTIONS: str = "_analysis_brief_sections"
"""Owner: agent reduce — sections from analysis brief."""

# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

ORACLE_ASSESSMENTS: str = "oracle_assessments"
"""Owner: oracle_runtime — assessment records from oracle."""

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

HARNESS_ENTRY_CONFIRMED: str = "harness_entry_confirmed"
"""Owner: harness / agent — whether harness entry point is confirmed."""

HARNESS_CONSUMPTION_CACHE_KEY: str = "_harness_consumption_cache_key"
"""Owner: harness — cache key for consumption model."""

HARNESS_RESOLUTION_ERROR: str = "harness_resolution_error"
"""Owner: state_init — error from harness resolution."""

# ---------------------------------------------------------------------------
# PoC recipe / build
# ---------------------------------------------------------------------------

POC_RECIPE: str = "poc_recipe"
"""Owner: poc_recipe / feedback — current PoC recipe."""

LAST_POC_BUILD_RESULT: str = "last_poc_build_result"
"""Owner: agent reduce — result of last PoC build attempt."""

CONSTRAINT_SOLUTIONS: str = "constraint_solutions"
"""Owner: poc_recipe — solutions discovered for constraints."""

DOMAIN_PACKS: str = "domain_packs"
"""Owner: poc_recipe — loaded domain packs (legacy dict format from knowledge registry)."""

KNOWLEDGE_RESULTS: str = "knowledge_results"
"""Owner: knowledge — DetectionResults from KnowledgeRegistry.select_packs()."""

KNOWLEDGE_BACKENDS: str = "knowledge_backends"
"""Owner: knowledge — BackendRegistry status snapshot."""

CARRIER_CONTRACT: str = "carrier_contract"
"""Owner: knowledge — confirmed CarrierContract from pack.derive_contract()."""

PACK_MODE: str = "pack_mode"
"""Owner: knowledge — active PackMode dict (mode, pack_id, detection_score, etc.)."""

PACK_RECIPE_PLAN: str = "pack_recipe_plan"
"""Owner: knowledge — RecipePlan dict from pack.plan() for confirmed format."""

PACK_PARSE_RESULT: str = "pack_parse_result"
"""Owner: knowledge — ParseResult dict from pack.parse() for confirmed format."""

# ---------------------------------------------------------------------------
# Frontier
# ---------------------------------------------------------------------------

FRONTIER_PROBES: str = "frontier_probes"
"""Owner: frontier_probe — frontier probe records."""

# ---------------------------------------------------------------------------
# Dynamic execution environment
# ---------------------------------------------------------------------------

STAGED_BINARY_CAPABILITY: str = "staged_binary_capability"
"""Owner: runtime/staged_binary — staged vulnerable binary capability."""

INVOCATION_PROFILE: str = "invocation_profile"
"""Owner: runtime/invocation_profile — resolved target invocation profile."""

RUNTIME_EVIDENCE: str = "runtime_evidence"
"""Owner: runtime/evidence — compact dynamic execution evidence ledger."""

RUNTIME_PROBE_BUDGETS: str = "runtime_probe_budgets"
"""Owner: runtime/evidence — per-candidate dynamic probe budget records."""

# ---------------------------------------------------------------------------
# Crash type
# ---------------------------------------------------------------------------

CRASH_TYPE_PRIOR: str = "crash_type_prior"
"""Owner: tracking_tools / feedback — prior crash type classification."""

CRASH_TYPE_SOURCE: str = "crash_type_source"
"""Owner: feedback — source of crash type information."""

CRASH_TYPE_PRIOR_SOURCE: str = "crash_type_prior_source"
"""Owner: feedback — source of prior crash type."""

# ---------------------------------------------------------------------------
# TUI / sidecar
# ---------------------------------------------------------------------------

TUI_PHASE: str = "_tui_phase"
"""Owner: agent reduce — phase label for TUI rendering."""

TRACE_RUN_DIR: str = "trace_run_dir"
"""Owner: state_init — directory for trace output."""

POC_OUTPUT_DIR: str = "poc_output_dir"
"""Owner: state_init — directory for PoC output."""

# ---------------------------------------------------------------------------
# Internal / bookkeeping
# ---------------------------------------------------------------------------

REDUCE_ROUND: str = "_reduce_round"
"""Owner: agent reduce — incrementing round counter."""

CRASH_LATCH_ROUND: str = "_crash_latch_round"
"""Owner: agent reduce — round at which crash latch was set."""

BUG_TYPE_CLASSIFIED: str = "_bug_type_classified"
"""Owner: state_init — flag that bug type was classified."""

COMPACTION_PRIORITY: str = "compaction_priority"
"""Owner: context — priority hint for compaction (high/critical)."""

# ---------------------------------------------------------------------------
# Task metadata (initialized once in state_init)
# ---------------------------------------------------------------------------

TASK_ROOT: str = "task_root"
"""Owner: state_init — root directory of current task."""

SEED_CORPUS_COUNT: str = "seed_corpus_count"
"""Owner: state_init — number of seed corpus files."""

REPO_SAMPLE_COUNT: str = "repo_sample_count"
"""Owner: state_init — number of repo sample files."""

REPO_INDEX_V2: str = "repo_index_v2"
"""Owner: state_init / tools — structural repo index."""

FUZZER_TARGETS: str = "fuzzer_targets"
"""Owner: state_init / tools — discovered fuzzer targets."""

FUZZER_TARGET: str = "fuzzer_target"
"""Owner: state_init — selected fuzzer target binary."""

RELEVANT_MEMORIES: str = "relevant_memories"
"""Owner: state_init — relevant memories loaded at init."""

FAMILY_MUTATION_HINTS: str = "family_mutation_hints"
"""Owner: candidates — per-family mutation hints."""

ACI_METRICS: str = "aci_metrics"
"""Owner: observations — ACI metrics for rendering."""

# ---------------------------------------------------------------------------
# Tool schema / payload
# ---------------------------------------------------------------------------

TOOL_SCHEMA_PAYLOAD_FILTERED: str = "tool_schema_payload_filtered"
"""Owner: agent init — marks that tool schema was filtered."""

TOOL_SCHEMA_PAYLOAD_FILTER_REASON: str = "tool_schema_payload_filter_reason"
"""Owner: agent init — reason for schema filtering."""

TOOL_SCHEMA_PAYLOAD_TOOL_COUNT: str = "tool_schema_tool_count"
"""Owner: agent init — tool count after filtering."""

EVIDENCE_MATCHES: str = "evidence_matches"
"""Owner: tools — evidence match results from search."""

# ---------------------------------------------------------------------------
# Compat: legacy snapshot fields (read-only fallback)
# ---------------------------------------------------------------------------

PATCH_DIFF: str = "patch_diff"
"""Compat: also stored as CyberGymState.patch_diff."""

ERROR_TXT: str = "error_txt"
"""Compat: also stored as CyberGymState.error_txt."""

DIFFICULTY: str = "difficulty"
"""Compat: also stored as CyberGymState.difficulty."""

SUBMITTED_CANDIDATE_FINGERPRINTS: str = "submitted_candidate_fingerprints"
"""Compat: also stored as CyberGymState.submitted_fingerprints."""

REPO_ARCHIVE_ROOT: str = "repo_archive_root"
"""Compat: also stored as CyberGymState.repo_archive_root."""

NEGATIVE_EVIDENCE: str = "negative_evidence"
"""Compat: negative evidence list (migrating to dedicated field)."""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _metadata(state: Any) -> MutableMapping[str, Any]:
    metadata = getattr(state, "metadata", None)
    if not isinstance(metadata, MutableMapping):
        metadata = {}
        setattr(state, "metadata", metadata)
    return metadata


def get_context_revision_map(state: Any) -> dict[str, int]:
    """Return context revisions, reading the legacy key as a fallback."""
    metadata = _metadata(state)
    current = metadata.get(CONTEXT_REVISIONS)
    legacy = metadata.get(LEGACY_CONTEXT_REVISIONS)
    source = current if isinstance(current, dict) else legacy
    if not isinstance(source, dict):
        return {}
    return {
        str(key): int(value or 0)
        for key, value in source.items()
        if str(key)
    }


def set_context_revision_map(state: Any, revisions: dict[str, int]) -> None:
    """Write context revisions to the canonical key only."""
    metadata = _metadata(state)
    normalized = {
        str(key): int(value or 0)
        for key, value in dict(revisions or {}).items()
        if str(key)
    }
    metadata[CONTEXT_REVISIONS] = normalized


def bump_context_revision_value(state: Any, key: str, *, allowed: set[str] | frozenset[str]) -> None:
    """Increment one context revision key using canonical metadata storage."""
    revision_key = key if key in allowed else "misc"
    revisions = get_context_revision_map(state)
    revisions[revision_key] = int(revisions.get(revision_key, 0) or 0) + 1
    set_context_revision_map(state, revisions)
