"""Core CyberGymState class for the Minimal CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from qitos.core.state import StateSchema

from .harness import (
    HarnessConsumptionEvidence,
    HarnessConsumptionModel,
    InputFormatModel,
    HarnessCandidate,
    HarnessResolution,
)
from .chain import ChainNode, ChainGate
from .investigation import DescriptionAnalysis, VerifiedCodeRef, SinkCandidate


@dataclass
class CyberGymState(StateSchema):
    """State for the Minimal CyberGym PoC Generation Agent.

    Tracks vulnerability context, investigation findings, and PoC iteration.
    No phase machine — the model decides its own workflow.
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

    # Harness info (populated from submit.sh)
    harness_info: str = ""
    submit_harness_targets: List[str] = field(default_factory=list)
    harness_candidates: List[HarnessCandidate] = field(default_factory=list)
    harness_resolution: HarnessResolution = field(default_factory=HarnessResolution)
    corpus_files: List[str] = field(default_factory=list)
    poc_strategy: str = ""
    input_format: InputFormatModel = field(default_factory=InputFormatModel)
    sink_candidates: List[SinkCandidate] = field(default_factory=list)
    search_anchors: List[str] = field(default_factory=list)
    active_sink_id: str = ""

    # File read tracking
    read_coverage: Dict[str, List[tuple]] = field(default_factory=dict)

    # Recent observation payload consumed by prepare()
    recent_tool_observations: List[str] = field(default_factory=list)

    # PoC iteration
    poc_attempts: int = 0
    last_error_trace: str = ""
    last_verification_result: Dict[str, Any] = field(default_factory=dict)
    last_submitted_poc_path: str = ""
    last_submitted_poc_hash: str = ""
    attempt_history: List[Dict[str, Any]] = field(default_factory=list)

    # PoC quality tracking
    best_poc_path: str = ""
    best_poc_score: int = 0  # 0=miss, 1=partial, 2=success
    discriminant_failed: bool = False
    consecutive_misses: int = 0
    consecutive_submit_errors: int = 0
    gdb_unavailable: bool = False
    gdb_call_count: int = 0
    gdb_calls_for_current_candidate: int = 0
    current_diagnosis_candidate: str = ""
    crash_type: str = ""
    crash_location: str = ""
    crash_stack: str = ""

    # Chain/gate tracking (for GATE tool)
    call_chain_nodes: List[ChainNode] = field(default_factory=list)
    call_chain_gates: List[ChainGate] = field(default_factory=list)
    gate_board_last_changed_step: int = 0
    gate_evidence_brief: Dict[str, str] = field(default_factory=dict)

    # Task-persistent memory — survives context compaction
    vulnerability_analysis: str = ""      # what/where/how trigger
    path_trace: List[str] = field(default_factory=list)
    attempt_history_compact: List[str] = field(default_factory=list)
    current_hypothesis: str = ""

    # Workspace paths
    workspace_root: str = ""
    repo_dir: str = ""

    # Promoted from metadata
    patch_diff: str = ""
    error_txt: str = ""
    harness_entry_confirmed: bool = False
    submitted_fingerprints: List[str] = field(default_factory=list)
    repo_archive_root: str = ""

    def __post_init__(self) -> None:
        parent_post_init = getattr(super(), "__post_init__", None)
        if callable(parent_post_init):
            parent_post_init()

        # Migrate promoted metadata keys
        if not self.patch_diff and self.metadata.get("patch_diff"):
            self.patch_diff = str(self.metadata["patch_diff"])
        if not self.error_txt and self.metadata.get("error_txt"):
            self.error_txt = str(self.metadata["error_txt"])
        if not self.submitted_fingerprints and self.metadata.get("submitted_candidate_fingerprints"):
            self.submitted_fingerprints = list(self.metadata["submitted_candidate_fingerprints"])
        if not self.repo_archive_root and self.metadata.get("repo_archive_root"):
            self.repo_archive_root = str(self.metadata["repo_archive_root"])

        # Normalize record lists
        if isinstance(self.description_analysis, dict):
            self.description_analysis = DescriptionAnalysis(**self.description_analysis)
        self.verified_search_refs = self._normalize_record_list(
            list(self.verified_search_refs or [])[:24], VerifiedCodeRef,
        )
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
        self.harness_entry_confirmed = (
            self.harness_resolution.status == "reachability_verified"
        )
        self.metadata["harness_entry_confirmed"] = self.harness_entry_confirmed
        self.input_format.confirmed = self.harness_entry_confirmed
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

    # ------------------------------------------------------------------
    # Record normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_record_list(items: List[Any], record_type: type[Any]) -> List[Any]:
        normalized: List[Any] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(record_type(**item))
            else:
                normalized.append(item)
        return normalized

    # ------------------------------------------------------------------
    # Chain-gate query helpers
    # ------------------------------------------------------------------

    def open_gates(self) -> List[ChainGate]:
        return [g for g in self.call_chain_gates if g.status in ("inferred", "unknown", "questioned")]

    def refuted_gates(self) -> List[ChainGate]:
        return [g for g in self.call_chain_gates if g.status == "refuted"]

    def confirmed_gates(self) -> List[ChainGate]:
        return [g for g in self.call_chain_gates if g.status == "confirmed"]

    def first_open_gate(self) -> ChainGate | None:
        open_gates = self.open_gates()
        return open_gates[0] if open_gates else None

    def _primary_sink_id(self) -> str:
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
        return [
            candidate for candidate in self.sink_candidates
            if candidate.status != "eliminated"
            and candidate.status != "provisional"
            and not bool((candidate.metadata or {}).get("requires_review"))
            and bool((candidate.metadata or {}).get("reviewed", candidate.source == "model_candidate"))
        ]

    def nodes_for_sink(self, sink_id: str) -> List[ChainNode]:
        primary = self._primary_sink_id()
        return [n for n in self.call_chain_nodes
                if n.sink_id == sink_id or (not n.sink_id and sink_id == primary)]

    def gates_for_sink(self, sink_id: str) -> List[ChainGate]:
        primary = self._primary_sink_id()
        return [g for g in self.call_chain_gates
                if g.sink_id == sink_id or (not g.sink_id and sink_id == primary)]

    def is_verified(self) -> bool:
        result = self.last_verification_result
        if not result:
            return False
        if result.get("accepted") is True:
            return True
        vul_code = result.get("vul_exit_code")
        fix_code = result.get("fix_exit_code")
        if vul_code is None:
            return False
        if vul_code != 0 and fix_code == 0:
            return True
        return False

    def vul_crashed(self) -> bool:
        result = self.last_verification_result
        if not result:
            return False
        vul_code = result.get("vul_exit_code")
        return vul_code is not None and vul_code != 0
