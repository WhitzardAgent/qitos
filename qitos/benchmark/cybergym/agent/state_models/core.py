"""Core CyberGymState class for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from qitos.core.state import StateSchema

from ..family_runtime import CandidateRecord, FamilyRecord, FeedbackRecord, FailureRecord
from .harness import (
    HarnessConsumptionEvidence,
    HarnessConsumptionModel,
    InputFormatModel,
    HarnessCandidate,
    HarnessResolution,
    HarnessSignal,
)
from .chain import PathConstraint, ChainNode, ChainGate
from .investigation import DescriptionAnalysis, VerifiedCodeRef, SinkCandidate


@dataclass
class CyberGymState(StateSchema):
    """State for the CyberGym PoC Generation Agent.

    Tracks vulnerability context, investigation findings, planning,
    PoC iteration progress, and phase tracking across the four-phase
    state machine (Ingestion -> Investigation -> Formulation -> Verification).
    """

    # Override defaults for PoC generation tasks
    max_steps: int = 30

    # Task profile
    task_profile: str = ""

    # Vulnerability context (stable across steps)
    vulnerability_description: str = ""
    cve_id: str = ""
    bug_type: str = ""  # buffer_overflow, use_after_free, integer_overflow, etc.
    vulnerability_hints: List[str] = field(default_factory=list)
    affected_component: str = ""

    # CyberGym task metadata
    task_id: str = ""
    agent_id: str = ""
    checksum: str = ""
    server_url: str = ""

    # Investigation findings
    vulnerable_files: List[str] = field(default_factory=list)
    vulnerable_functions: List[str] = field(default_factory=list)
    input_entry_points: List[str] = field(default_factory=list)  # DEPRECATED: unused, kept for serialization compat
    trigger_hypothesis: str = ""
    repo_index: str = ""
    vulnerability_class: str = ""
    expected_signal: str = ""
    input_vector_hints: List[str] = field(default_factory=list)
    likely_entrypoints: List[str] = field(default_factory=list)
    likely_fuzz_targets: List[str] = field(default_factory=list)
    source_files_mentioned: List[str] = field(default_factory=list)
    symbols_mentioned: List[str] = field(default_factory=list)
    task_spec_confidence: float = 0.0
    description_analysis: DescriptionAnalysis = field(default_factory=DescriptionAnalysis)
    verified_search_refs: List[VerifiedCodeRef] = field(default_factory=list)
    unresolved_search_hints: List[str] = field(default_factory=list)

    # Harness info (populated during ingestion from submit.sh)
    harness_info: str = ""  # binary path and arguments from submit.sh
    submit_harness_targets: List[str] = field(default_factory=list)
    harness_candidates: List[HarnessCandidate] = field(default_factory=list)
    harness_resolution: HarnessResolution = field(default_factory=HarnessResolution)
    corpus_files: List[str] = field(default_factory=list)  # discovered fuzzing corpus/sample files
    poc_strategy: str = ""  # auto-detected: text, binary_python, corpus_mutate, hex
    input_format: InputFormatModel = field(default_factory=InputFormatModel)
    sink_candidates: List[SinkCandidate] = field(default_factory=list)
    search_anchors: List[str] = field(default_factory=list)
    exploration_complete: bool = False  # set True when agent has enough understanding
    active_sink_id: str = ""  # Currently targeted sink candidate
    sink_hypothesis_source: str = ""  # "model_candidate", "auto_promoted", "asan_feedback"
    latest_sink_analysis_brief: Dict[str, Any] = field(default_factory=dict)
    active_sink_candidate_id: str = ""
    latest_brief_id: str = ""
    selected_analysis_path_id: str = ""
    open_analysis_unresolved_ids: List[str] = field(default_factory=list)
    analysis_status: str = "NO_TARGET"
    injected_brief_fingerprint: str = ""
    latest_analysis_mode: str = ""  # "automatic" | "interactive" | ""
    analysis_graph_id: str = ""
    analysis_index_status: str = "NO_INDEX"
    analysis_index_coverage: Dict[str, Any] = field(default_factory=dict)
    latest_read_analysis: Dict[str, Any] = field(default_factory=dict)
    latest_read_analysis_fingerprint: str = ""
    injected_read_analysis_fingerprint: str = ""
    injected_index_fingerprint: str = ""
    sink_search_leads: List[Dict[str, Any]] = field(default_factory=list)
    reachable_function_candidates: List[Dict[str, Any]] = field(default_factory=list)
    ranked_vulnerability_paths: List[Dict[str, Any]] = field(default_factory=list)
    ranked_paths_graph_id: str = ""
    ranked_paths_status: str = ""
    latest_sink_search_brief: Dict[str, Any] = field(default_factory=dict)
    latest_sink_search_brief_id: str = ""
    sink_search_fingerprint: str = ""
    injected_sink_search_fingerprint: str = ""

    # File read tracking — which files/line ranges have been read
    read_coverage: Dict[str, List[tuple]] = field(default_factory=dict)

    # Cumulative active runtime across resumes (seconds). Persisted every step so a
    # restart can continue the same time budget instead of granting a fresh one.
    runtime_elapsed_seconds: float = 0.0  # DEPRECATED: unused, kept for serialization compat

    # Planning
    plan: List[str] = field(default_factory=list)
    plan_cursor: int = 0

    # PoC iteration
    poc_attempts: int = 0
    last_error_trace: str = ""
    last_verification_result: Dict[str, Any] = field(default_factory=dict)
    pending_attempt_record: bool = False
    pending_reflection: bool = False
    pending_chain_checkpoint: bool = False
    pending_gates_checkpoint: bool = False
    pending_sink_checkpoint: bool = False
    last_recorded_poc_id: str = ""  # DEPRECATED: unused, kept for serialization compat
    last_submitted_poc_path: str = ""
    last_submitted_poc_hash: str = ""
    attempt_history: List[Dict[str, Any]] = field(default_factory=list)
    exploration_notes: List[Dict[str, Any]] = field(default_factory=list)

    # PoC quality tracking (regression protection)
    best_poc_path: str = ""
    best_poc_score: int = 0  # 0=miss, 1=partial(vul crashes), 2=success(discriminant)
    discriminant_failed: bool = False  # True when fix_exit != 0 (PoC too aggressive)
    consecutive_misses: int = 0  # consecutive NO_TRIGGER submits (resets on any crash)
    consecutive_submit_errors: int = 0  # consecutive submit_poc errors (not verification results)
    pending_reproduction: bool = False  # set after no-trigger submit, cleared by gdb_debug
    gdb_unavailable: bool = False  # latched when gdb is confirmed unavailable for this task
    phase_submissions: int = 0  # submit_poc count in current phase (resets on phase transition)
    crash_type: str = ""  # parsed from sanitizer output (e.g., heap-buffer-overflow)
    crash_location: str = ""  # parsed from sanitizer output (file:line)
    crash_stack: str = ""  # ASAN/MSAN/UBSAN stack summary (top function names)

    # Phase tracking
    current_phase: str = "ingestion"  # ingestion | exploration | investigation | formulation | verification
    phase_enter_step: int = 0
    phase_local_steps: int = 0
    control_mode: str = "orienting"
    mode_enter_step: int = 0
    mode_local_steps: int = 0

    # Lightweight budgeting signals used to keep formulation action-oriented
    phase_read_actions: int = 0
    repeated_read_target: str = ""
    repeated_read_count: int = 0

    # Recent observation packet payload, consumed by prepare()
    recent_tool_observations: List[str] = field(default_factory=list)

    # Lightweight self-review state
    repeated_failure_signature: str = ""
    repeated_failure_count: int = 0
    reflection_note: str = ""
    reflection_history: List[Dict[str, Any]] = field(default_factory=list)
    reinvestigate_requested: bool = False
    pending_reminder: str = ""
    pending_reminder_signature: str = ""
    pending_reminders: List[str] = field(default_factory=list)
    reminder_cooldowns: Dict[str, int] = field(default_factory=dict)
    verification_history: List[Dict[str, Any]] = field(default_factory=list)
    failure_history: List[FailureRecord] = field(default_factory=list)
    candidate_required: bool = False

    # Multi-agent runtime primitives
    family_pool: List[FamilyRecord] = field(default_factory=list)
    candidate_queue: List[CandidateRecord] = field(default_factory=list)
    ready_pocs: List[CandidateRecord] = field(default_factory=list)
    submitted_candidate_index: Dict[str, str] = field(default_factory=dict)
    feedback_history: List[FeedbackRecord] = field(default_factory=list)
    hot_feedback_window: List[FeedbackRecord] = field(default_factory=list)
    evidence_index: Dict[str, Any] = field(default_factory=dict)
    harness_signals: List[HarnessSignal] = field(default_factory=list)
    path_constraints: List[PathConstraint] = field(default_factory=list)
    # Ordered entry-to-sink call chain (replaces flat path_constraints)
    call_chain_nodes: List[ChainNode] = field(default_factory=list)
    call_chain_gates: List[ChainGate] = field(default_factory=list)
    # Candidate constraints auto-extracted from code but NOT yet confirmed by LLM.
    # Presented as "Suggested Constraints" in the observation for LLM to judge.
    # LLM should use record_gate to promote relevant ones to call_chain_gates.
    suggested_constraints: List[Dict[str, Any]] = field(default_factory=list)
    constraint_paths: List[Dict[str, Any]] = field(default_factory=list)
    constraint_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    active_input_mappings: List[Dict[str, Any]] = field(default_factory=list)
    gate_board_last_changed_step: int = 0
    gate_evidence_brief: Dict[str, str] = field(default_factory=dict)
    runtime_stage: str = "bootstrap"
    durable_project_memory: Dict[str, Any] = field(default_factory=dict)
    durable_code_facts: List[str] = field(default_factory=list)
    durable_feedback_facts: List[str] = field(default_factory=list)

    # Task-persistent memory — survives context compaction.
    # Updated in reduce() at every step; rendered in every observation.
    vulnerability_analysis: str = ""      # max 600 chars: what/where/how trigger
    path_trace: List[str] = field(default_factory=list)   # max 8: entry→sink links
    attempt_history_compact: List[str] = field(default_factory=list)  # max 10: attempt+outcome
    current_hypothesis: str = ""         # max 400 chars: what to try next and why

    # Structured IR fields (static analysis bundle)
    crash_mechanism_graphs: List[Dict[str, Any]] = field(default_factory=list)
    active_trigger_objectives: List[Dict[str, Any]] = field(default_factory=list)
    protocol_transcript_plans: List[Dict[str, Any]] = field(default_factory=list)
    structured_rewrite_plans: List[Dict[str, Any]] = field(default_factory=list)
    consistency_signals: List[Dict[str, Any]] = field(default_factory=list)
    local_mining_refs: List[Dict[str, Any]] = field(default_factory=list)
    harness_protocols: List[Dict[str, Any]] = field(default_factory=list)

    # Workspace paths
    workspace_root: str = ""
    repo_dir: str = ""  # path to extracted repo inside workspace

    # Promoted from metadata for type safety (metadata remains for backward compat)
    patch_diff: str = ""
    error_txt: str = ""
    harness_entry_confirmed: bool = False
    submitted_fingerprints: List[str] = field(default_factory=list)
    repo_archive_root: str = ""

    def __post_init__(self) -> None:
        parent_post_init = getattr(super(), "__post_init__", None)
        if callable(parent_post_init):
            parent_post_init()

        # Migrate promoted metadata keys (metadata remains as fallback)
        if not self.patch_diff and self.metadata.get("patch_diff"):
            self.patch_diff = str(self.metadata["patch_diff"])
        if not self.error_txt and self.metadata.get("error_txt"):
            self.error_txt = str(self.metadata["error_txt"])
        if not self.submitted_fingerprints and self.metadata.get("submitted_candidate_fingerprints"):
            self.submitted_fingerprints = list(self.metadata["submitted_candidate_fingerprints"])
        if not self.repo_archive_root and self.metadata.get("repo_archive_root"):
            self.repo_archive_root = str(self.metadata["repo_archive_root"])

        self.family_pool = self._normalize_record_list(self.family_pool, FamilyRecord)
        self.candidate_queue = self._normalize_record_list(self.candidate_queue, CandidateRecord)
        self.ready_pocs = self._normalize_record_list(self.ready_pocs, CandidateRecord)
        self.feedback_history = self._normalize_record_list(self.feedback_history, FeedbackRecord)
        self.hot_feedback_window = self._normalize_record_list(self.hot_feedback_window, FeedbackRecord)
        self.failure_history = self._normalize_record_list(self.failure_history, FailureRecord)
        self.harness_signals = self._normalize_record_list(self.harness_signals, HarnessSignal)
        if isinstance(self.description_analysis, dict):
            self.description_analysis = DescriptionAnalysis(**self.description_analysis)
        self.verified_search_refs = self._normalize_record_list(
            list(self.verified_search_refs or [])[:24], VerifiedCodeRef,
        )
        self.unresolved_search_hints = [
            str(item) for item in list(self.unresolved_search_hints or [])[:24] if str(item).strip()
        ]
        self.ranked_vulnerability_paths = [
            item for item in list(self.ranked_vulnerability_paths or [])[:5]
            if isinstance(item, dict)
        ]
        self.active_input_mappings = [
            item for item in list(self.active_input_mappings or [])[:8]
            if isinstance(item, dict)
        ]
        self.crash_mechanism_graphs = [
            item for item in list(self.crash_mechanism_graphs or [])[:5]
            if isinstance(item, dict)
        ]
        self.active_trigger_objectives = [
            item for item in list(self.active_trigger_objectives or [])[:8]
            if isinstance(item, dict)
        ]
        self.protocol_transcript_plans = [
            item for item in list(self.protocol_transcript_plans or [])[:4]
            if isinstance(item, dict)
        ]
        self.structured_rewrite_plans = [
            item for item in list(self.structured_rewrite_plans or [])[:6]
            if isinstance(item, dict)
        ]
        self.consistency_signals = [
            item for item in list(self.consistency_signals or [])[:10]
            if isinstance(item, dict)
        ]
        self.local_mining_refs = [
            item for item in list(self.local_mining_refs or [])[:10]
            if isinstance(item, dict)
        ]
        self.harness_protocols = [
            item for item in list(self.harness_protocols or [])[:5]
            if isinstance(item, dict)
        ]
        self.harness_candidates = self._normalize_record_list(self.harness_candidates, HarnessCandidate)
        if isinstance(self.harness_resolution, dict):
            self.harness_resolution = HarnessResolution(**self.harness_resolution)
        if isinstance(self.input_format, dict):
            self.input_format = InputFormatModel(**self.input_format)
        if isinstance(getattr(self.input_format, "consumption", None), dict):
            self.input_format.consumption = HarnessConsumptionModel(
                **self.input_format.consumption
            )
        self.input_format.consumption.evidence = self._normalize_record_list(
            list(getattr(self.input_format.consumption, "evidence", []) or [])[:12],
            HarnessConsumptionEvidence,
        )
        # The legacy boolean is a compatibility projection, never an
        # independent source of truth.
        self.harness_entry_confirmed = (
            self.harness_resolution.status == "reachability_verified"
        )
        self.metadata["harness_entry_confirmed"] = self.harness_entry_confirmed
        self.input_format.confirmed = self.harness_entry_confirmed
        self.path_constraints = self._normalize_record_list(self.path_constraints, PathConstraint)
        self.call_chain_nodes = self._normalize_record_list(self.call_chain_nodes, ChainNode)
        self.call_chain_gates = self._normalize_record_list(self.call_chain_gates, ChainGate)
        self.sink_candidates = self._normalize_record_list(self.sink_candidates, SinkCandidate)
        for candidate in self.sink_candidates:
            candidate.metadata = dict(candidate.metadata or {})
            if not candidate.file and candidate.location:
                raw_file, sep, raw_line = candidate.location.rpartition(":")
                candidate.file = raw_file if sep and raw_line.isdigit() else candidate.location
                candidate.line = int(raw_line) if sep and raw_line.isdigit() else candidate.line
            if not candidate.reason:
                candidate.reason = candidate.evidence
            if not candidate.candidate_id:
                import hashlib
                material = f"{candidate.repository_id}|{candidate.file}|{candidate.line}|{candidate.function}|{candidate.callee}|{candidate.expression}"
                candidate.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
            if "reviewed" not in candidate.metadata:
                candidate.metadata["reviewed"] = (
                    candidate.source == "model_candidate"
                    and not bool(candidate.metadata.get("requires_review"))
                )
            if "selection_status" not in candidate.metadata:
                candidate.metadata["selection_status"] = (
                    "unreviewed" if bool(candidate.metadata.get("requires_review")) else "active"
                )
            if "candidate_role" not in candidate.metadata:
                candidate.metadata["candidate_role"] = (
                    candidate.metadata.get("role")
                    or ("crash_site" if candidate.source == "model_candidate" else "unknown")
                )
            if candidate.metadata.get("candidate_role") == "path_anchor":
                candidate.metadata["needs_downstream_endpoint"] = True

        # Migrate legacy path_constraints → call_chain_gates (one-time)
        if self.path_constraints and not self.call_chain_gates:
            self._migrate_path_constraints_to_chain()

    # ------------------------------------------------------------------
    # PoC recipe accessors
    # ------------------------------------------------------------------

    def get_poc_recipe(self) -> Dict[str, Any]:
        """Return the current PoC recipe dict (never None)."""
        if not isinstance(self.metadata, dict):
            return {}
        recipe = self.metadata.get("poc_recipe")
        if not isinstance(recipe, dict):
            recipe = {}
            self.metadata["poc_recipe"] = recipe
        return recipe

    def update_poc_recipe(self, **kwargs: Any) -> None:
        """Update specific fields in the PoC recipe."""
        recipe = self.get_poc_recipe()
        recipe.update(kwargs)

    def add_trigger_mutation(self, mutation: Dict[str, Any]) -> None:
        """Add a trigger mutation target to the recipe."""
        recipe = self.get_poc_recipe()
        mutations = recipe.setdefault("trigger_mutations", [])
        # Deduplicate by mapping_id
        mid = mutation.get("mapping_id", "")
        if mid:
            mutations[:] = [m for m in mutations if m.get("mapping_id") != mid]
        mutations.append(mutation)
        recipe["trigger_mutations"] = mutations[-6:]  # cap at 6

    def add_recipe_gap(self, gap: str) -> None:
        """Add an open mapping gap to the recipe."""
        recipe = self.get_poc_recipe()
        gaps = recipe.setdefault("open_gaps", [])
        if gap not in gaps:
            gaps.append(gap)
        recipe["open_gaps"] = gaps[:8]  # cap at 8

    # ------------------------------------------------------------------
    # Negative evidence
    # ------------------------------------------------------------------

    _NEGATIVE_EVIDENCE_KINDS = frozenset({
        "no_crash_unknown", "path_not_reached", "path_reached_no_trigger",
        "trigger_condition_not_satisfied", "format_error", "carrier_sanity_fail",
        "unreachable_path", "wrong_crash", "repeated_candidate", "bad_seed",
        # Structured analysis scoped kinds
        "objective_not_satisfied", "transcript_order_mismatch",
        "transcript_endpoint_mismatch", "structured_rewrite_invalid",
        "consistency_block", "wrong_harness_binary", "wrong_format_scope",
        "sanitizer_origin_missed", "objective_not_observable",
        "oracle_not_observable", "frontier_unknown",
    })
    _NEGATIVE_EVIDENCE_CAP = 20
    _NEGATIVE_EVIDENCE_DEFAULT_TTL = 8

    def append_negative_evidence(
        self,
        *,
        kind: str,
        candidate_id: str = "",
        ranked_path_id: str = "",
        mapping_id: str = "",
        family_id: str = "",
        objective_id: str = "",
        transcript_id: str = "",
        rewrite_id: str = "",
        consistency_signal_id: str = "",
        summary: str,
        avoid_next: str = "",
        ttl: int = 0,
    ) -> str:
        """Append a typed negative evidence record; returns evidence_id."""
        if kind not in self._NEGATIVE_EVIDENCE_KINDS:
            kind = "no_crash_unknown"
        ne_list: List[Dict[str, Any]] = self.metadata.setdefault("negative_evidence", [])
        evidence_id = f"ne_{len(ne_list):04d}"
        record: Dict[str, Any] = {
            "evidence_id": evidence_id,
            "kind": kind,
            "candidate_id": candidate_id,
            "ranked_path_id": ranked_path_id,
            "mapping_id": mapping_id,
            "family_id": family_id,
            "objective_id": objective_id,
            "transcript_id": transcript_id,
            "rewrite_id": rewrite_id,
            "consistency_signal_id": consistency_signal_id,
            "summary": summary,
            "avoid_next": avoid_next,
            "created_step": self.current_step,
            "ttl": ttl or self._NEGATIVE_EVIDENCE_DEFAULT_TTL,
        }
        ne_list.append(record)
        # Cap: keep most recent
        if len(ne_list) > self._NEGATIVE_EVIDENCE_CAP:
            self.metadata["negative_evidence"] = ne_list[-self._NEGATIVE_EVIDENCE_CAP:]
        return evidence_id

    def recent_negative_evidence(self, limit: int = 5, kind: str = "") -> List[Dict[str, Any]]:
        """Return most recent negative evidence, optionally filtered by kind."""
        ne_list: List[Dict[str, Any]] = self.metadata.get("negative_evidence", [])
        # Decay TTL
        for rec in ne_list:
            rec["ttl"] = max(0, rec.get("ttl", 0) - 1)
        # Remove expired
        ne_list = [r for r in ne_list if r["ttl"] > 0]
        self.metadata["negative_evidence"] = ne_list
        if kind:
            ne_list = [r for r in ne_list if r.get("kind") == kind]
        return ne_list[-limit:]

    def recent_negative_evidence_for_scope(
        self,
        *,
        objective_id: str = "",
        transcript_id: str = "",
        rewrite_id: str = "",
        consistency_signal_id: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return recent negative evidence filtered by scope ids."""
        ne_list: List[Dict[str, Any]] = self.metadata.get("negative_evidence", [])
        active = [r for r in ne_list if r.get("ttl", 0) > 0]
        if objective_id:
            active = [r for r in active if r.get("objective_id") == objective_id]
        if transcript_id:
            active = [r for r in active if r.get("transcript_id") == transcript_id]
        if rewrite_id:
            active = [r for r in active if r.get("rewrite_id") == rewrite_id]
        if consistency_signal_id:
            active = [r for r in active if r.get("consistency_signal_id") == consistency_signal_id]
        return active[-limit:]

    def evidence_blocks_action(
        self,
        action: str = "submit",
        family_id: str = "",
        objective_id: str = "",
        transcript_id: str = "",
    ) -> bool:
        """Check whether accumulated negative evidence should block an action.

        Current rules:
        - submit: blocked if ≥3 no-trigger evidences for same family_id
        - submit: blocked if carrier_sanity_fail / structured_rewrite_invalid / consistency_block exists
        - submit: blocked if same objective has ≥2 objective_not_satisfied evidences
        - submit: blocked if transcript has ≥1 transcript_endpoint_mismatch evidence
        """
        ne_list: List[Dict[str, Any]] = self.metadata.get("negative_evidence", [])
        active = [r for r in ne_list if r.get("ttl", 0) > 0]
        if action == "submit":
            # Hard blocks: any carrier/rewrite/consistency fail
            hard_block_kinds = {"carrier_sanity_fail", "structured_rewrite_invalid", "consistency_block"}
            if any(r.get("kind") in hard_block_kinds for r in active):
                return True
            # Same-family no-trigger
            if family_id:
                same_family = [
                    r for r in active
                    if r.get("family_id") == family_id
                    and r.get("kind") in ("path_reached_no_trigger", "no_crash_unknown")
                ]
                if len(same_family) >= 3:
                    return True
            # Same objective repeated miss
            if objective_id:
                same_obj = [
                    r for r in active
                    if r.get("objective_id") == objective_id
                    and r.get("kind") == "objective_not_satisfied"
                ]
                if len(same_obj) >= 2:
                    return True
            # Transcript mismatch
            if transcript_id:
                same_tr = [
                    r for r in active
                    if r.get("transcript_id") == transcript_id
                    and r.get("kind") == "transcript_endpoint_mismatch"
                ]
                if len(same_tr) >= 1:
                    return True
        return False

    @staticmethod
    def _normalize_record_list(items: List[Any], record_type: type[Any]) -> List[Any]:
        normalized: List[Any] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(record_type(**item))
            else:
                normalized.append(item)
        return normalized

    def _migrate_path_constraints_to_chain(self) -> None:
        """Convert legacy PathConstraint entries to ChainGate objects."""
        for pc in self.path_constraints:
            gate = ChainGate(
                node_order=0,
                gate_type=pc.constraint_type,
                description=pc.description,
                required_condition=pc.required_values,
                status=pc.status if pc.status != "hypothesized" else "inferred",
                evidence=f"Legacy constraint from {pc.source_location}" if pc.source_location else "",
                repair_hint="",
            )
            # Deduplicate by description
            if not any(g.description == gate.description for g in self.call_chain_gates):
                self.call_chain_gates.append(gate)

    # ------------------------------------------------------------------
    # Chain-gate query helpers
    # ------------------------------------------------------------------

    def open_gates(self) -> List[ChainGate]:
        """Gates that are not yet confirmed or bypassed."""
        return [g for g in self.call_chain_gates if g.status in ("inferred", "unknown", "questioned")]

    def refuted_gates(self) -> List[ChainGate]:
        """Gates that were refuted — key learning signal."""
        return [g for g in self.call_chain_gates if g.status == "refuted"]

    def confirmed_gates(self) -> List[ChainGate]:
        """Gates that have been confirmed from source code."""
        return [g for g in self.call_chain_gates if g.status == "confirmed"]

    def first_open_gate(self) -> ChainGate | None:
        """The earliest unresolved gate — the primary blocker."""
        open_gates = self.open_gates()
        return open_gates[0] if open_gates else None

    def _primary_sink_id(self) -> str:
        """Best reviewed candidate; static leads do not count until reviewed."""
        active = self.confirmed_sink_candidates()
        if not active:
            return ""
        best = max(active, key=self._sink_candidate_priority)
        return f"{best.function}@{best.location}"

    @staticmethod
    def _sink_candidate_priority(candidate: SinkCandidate) -> tuple[int, int, float]:
        meta = dict(candidate.metadata or {})
        role = str(meta.get("candidate_role") or meta.get("role") or "unknown")
        paired = bool(meta.get("paired_with"))
        role_score = {
            "crash_site": 50,
            "causal_site": 40 if paired else 30,
            "dangerous_primitive": 35,
            "path_anchor": 10,
            "unknown": 20,
        }.get(role, 15)
        if meta.get("selection_status") == "cooldown":
            role_score -= 100
        reviewed_score = 1 if bool(meta.get("reviewed")) else 0
        return (role_score, reviewed_score, float(candidate.confidence or 0.0))

    def confirmed_sink_candidates(self) -> List[SinkCandidate]:
        from ..analysis.vuln_patterns import is_entry_point_function
        provisional_sources = {
            "static_navigation", "description", "harness_chain",
            "graph_auto_deepen",
        }
        return [
            candidate for candidate in self.sink_candidates
            if candidate.status != "eliminated"
            and candidate.status != "provisional"
            and candidate.source not in provisional_sources
            # Also check original_source from auto-promotion to prevent
            # noise candidates that were promoted from provisional sources
            and str((candidate.metadata or {}).get("original_source") or candidate.source or "") not in provisional_sources
            and not bool((candidate.metadata or {}).get("requires_review"))
            and bool((candidate.metadata or {}).get("reviewed", candidate.source == "model_candidate"))
            and not is_entry_point_function(candidate.function)
        ]

    def navigation_candidates(self) -> List[SinkCandidate]:
        return [
            candidate for candidate in self.sink_candidates
            if candidate.status != "eliminated"
            and (candidate.source == "static_navigation" or bool((candidate.metadata or {}).get("requires_review")))
        ]

    def nodes_for_sink(self, sink_id: str) -> List[ChainNode]:
        """Get chain nodes for a specific sink candidate."""
        primary = self._primary_sink_id()
        return [n for n in self.call_chain_nodes
                if n.sink_id == sink_id or (not n.sink_id and sink_id == primary)]

    def gates_for_sink(self, sink_id: str) -> List[ChainGate]:
        """Get gates for a specific sink candidate."""
        primary = self._primary_sink_id()
        return [g for g in self.call_chain_gates
                if g.sink_id == sink_id or (not g.sink_id and sink_id == primary)]

    def derive_numerical_constraints(self) -> List[str]:
        """Derive concrete numeric constraints from code facts × gate conditions.

        Scans durable_code_facts for numeric values (#define, buffer_size,
        field_offset, etc.) and cross-references with confirmed bounds_gate
        and value_gate conditions to produce concrete constraints the LLM
        can use directly in PoC construction.
        """
        import re as _re
        lines: List[str] = []
        facts = list(self.durable_code_facts or [])
        gates = self.confirmed_gates()

        # Extract numeric values from code facts
        numeric_values = {}
        for fact in facts:
            # const: NAME = VALUE
            m = _re.match(
                r'(?:const|buffer_size|array_size|struct_size)\s*:\s*(\w+)\s*=\s*(0x[\da-fA-F]+|\d+)',
                fact,
            )
            if m:
                name, val = m.group(1), m.group(2)
                numeric_values[name] = int(val, 16) if val.startswith('0x') else int(val)
                continue
            # field_offset: NAME = VALUE
            m = _re.match(r'field_offset\s*:\s*(\w+)\s*=\s*(0x[\da-fA-F]+|\d+)', fact)
            if m:
                name, val = m.group(1), m.group(2)
                numeric_values[name] = int(val, 16) if val.startswith('0x') else int(val)
                continue
            # func_signature with numeric constants like "buffer[8192]"
            m = _re.search(r'(\w+)\[(\d+)\]', fact)
            if m:
                numeric_values[f"{m.group(1)}_size"] = int(m.group(2))

        if not numeric_values and not gates:
            return lines

        # List known numeric values (largest first for relevance)
        for name, value in sorted(numeric_values.items(), key=lambda x: -x[1]):
            lines.append(f"{name} = {value} (0x{value:x})")

        # Cross-reference gates with numeric values
        for g in gates:
            cond = g.required_condition or ""
            desc = g.description or ""
            if g.gate_type == "bounds_gate":
                # Look for variable references in the condition
                matched = False
                for var_name, var_val in numeric_values.items():
                    base_name = var_name.replace("_size", "").replace("_len", "")
                    if base_name in cond or var_name in cond:
                        lines.append(
                            f"→ bounds_gate: {cond} "
                            f"⇒ {var_name}={var_val}, overflow starts at offset ≥ {var_val}"
                        )
                        matched = True
                        break
                if not matched and cond:
                    lines.append(f"→ bounds_gate: {cond}")
            elif g.gate_type == "format_gate":
                if "0x" in cond or "bytes" in cond.lower() or "magic" in cond.lower():
                    lines.append(f"→ format_gate: {cond}")
            elif g.gate_type == "value_gate":
                if cond:
                    lines.append(f"→ value_gate: {cond}")
            elif g.gate_type == "dispatch_gate":
                if cond:
                    lines.append(f"→ dispatch_gate: {cond}")

        return lines[:12]

    def is_verified(self) -> bool:
        """Check if the PoC has been verified as successful by the server."""
        result = self.last_verification_result
        if not result:
            return False
        if result.get("accepted") is True:
            return True
        vul_code = result.get("vul_exit_code")
        fix_code = result.get("fix_exit_code")
        # Success requires the vulnerable binary to fail and the fixed binary to
        # remain clean. Public vul-only feedback is useful for refinement but is
        # not a verified exploit.
        if vul_code is None:
            return False
        if vul_code != 0 and fix_code == 0:
            return True
        return False

    def vul_crashed(self) -> bool:
        """VUL-SIDE-ONLY signal: did the last submitted candidate crash the
        vulnerable binary? This is the only feedback the official CyberGym
        protocol exposes to the agent (public /submit-vul). Used as the agent's
        own stop signal so it never relies on the private fix-side verdict.
        Scoring (is_verified) still uses the fix discriminant separately."""
        result = self.last_verification_result
        if not result:
            return False
        vul_code = result.get("vul_exit_code")
        return vul_code is not None and vul_code != 0
