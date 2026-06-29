"""Typed state for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from qitos.core.state import StateSchema

from .family_runtime import CandidateRecord, FamilyRecord, FeedbackRecord, FailureRecord


@dataclass
class InputFormatModel:
    """Structured model of what the harness expects as input.

    Populated incrementally: auto-detected from corpus/harness_info on init,
    then confirmed when source code reveals the entry function signature.
    """

    format_type: str = ""          # png, jpeg, pdf, zip, text, elf, ...
    entry_point: str = ""          # LLVMFuzzerTestOneInput, main, ...
    input_path: str = ""           # stdin, file_argv, buffer
    magic_bytes: str = ""          # expected magic number (hex string)
    sample_paths: List[str] = field(default_factory=list)
    mutation_strategy: str = ""    # corpus_mutate, handcraft, text, hex, binary_python
    container_structure: str = ""  # e.g., "CFF2 inside SFNT inside OTF"
    size_constraints: str = ""     # e.g., "max 1MB, declared_size at offset 4"
    confirmed: bool = False        # confirmed from source code vs inferred


@dataclass
class HarnessSignal:
    """Structured signal about the task harness or fuzzer target."""

    name: str
    source: str = ""
    evidence: str = ""
    confidence: float = 0.0


@dataclass
class PathConstraint:
    """One evidence-backed or open condition on the entry-to-sink path.

    DEPRECATED: retained for serialization compat.  New code should use
    ChainNode + ChainGate instead.
    """

    description: str
    source_location: str = ""
    status: str = "unknown"  # confirmed | hypothesized | unknown
    required_values: str = ""
    constraint_type: str = "path_gate"


@dataclass
class ChainNode:
    """One node in the ordered entry-to-sink call chain.

    Nodes are ordered from harness entry (order=0) to the vulnerability
    sink (highest order).  Each node records the function, its role in
    the data-flow chain, and whether the agent has confirmed it from
    source code.
    """

    location: str        # e.g. "attribute.c:1880"
    function: str        # e.g. "GenerateEXIFAttribute"
    role: str            # "entry" | "parser" | "dispatch" | "guard" | "sink"
    description: str     # e.g. "IFD entry parsing loop"
    status: str          # "confirmed" | "inferred" | "unknown"
    evidence: str        # e.g. "READ attribute.c:1870-1910"
    order: int           # Position in chain (0 = harness entry)


@dataclass
class ChainGate:
    """A condition at a ChainNode that input must satisfy to reach the sink.

    Gates represent **positive constraints**: "what must be true" for the
    PoC to pass through this point in the call chain.  When a submission
    fails, gates are *refuted* (not deleted) so the agent learns from
    failures and derives repair hints.
    """

    node_order: int      # Which ChainNode this gate belongs to
    gate_type: str       # "format_gate" | "path_gate" | "dispatch_gate" | "bounds_gate" | "value_gate"
    description: str     # e.g. "Must match 'Exif\\0\\0' magic (memcmp at attribute.c:1865)"
    required_condition: str  # Positive condition for PoC construction
    status: str          # "confirmed" | "inferred" | "refuted" | "bypassed"
    evidence: str        # e.g. "READ attribute.c:1887 — overflow detection present"
    repair_hint: str     # e.g. "Try oval+n wrap-around instead of n=0"


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

    # Harness info (populated during ingestion from submit.sh)
    harness_info: str = ""  # binary path and arguments from submit.sh
    corpus_files: List[str] = field(default_factory=list)  # discovered fuzzing corpus/sample files
    poc_strategy: str = ""  # auto-detected: text, binary_python, corpus_mutate, hex
    input_format: InputFormatModel = field(default_factory=InputFormatModel)

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
    phase_submissions: int = 0  # submit_poc count in current phase (resets on phase transition)
    crash_type: str = ""  # parsed from sanitizer output (e.g., heap-buffer-overflow)
    crash_location: str = ""  # parsed from sanitizer output (file:line)

    # Phase tracking
    current_phase: str = "ingestion"  # ingestion | investigation | formulation | verification
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
    suggested_constraints: List[Dict[str, str]] = field(default_factory=list)
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
        if not self.harness_entry_confirmed and self.metadata.get("harness_entry_confirmed"):
            self.harness_entry_confirmed = bool(self.metadata["harness_entry_confirmed"])
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
        self.path_constraints = self._normalize_record_list(self.path_constraints, PathConstraint)
        self.call_chain_nodes = self._normalize_record_list(self.call_chain_nodes, ChainNode)
        self.call_chain_gates = self._normalize_record_list(self.call_chain_gates, ChainGate)

        # Migrate legacy path_constraints → call_chain_gates (one-time)
        if self.path_constraints and not self.call_chain_gates:
            self._migrate_path_constraints_to_chain()

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
        return [g for g in self.call_chain_gates if g.status in ("inferred", "unknown")]

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
