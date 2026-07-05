"""Harness-related data models for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class HarnessConsumptionEvidence:
    """One source-backed observation about how the harness consumes input."""

    kind: str
    expression: str
    file: str = ""
    line: int = 0
    confidence: float = 0.0


@dataclass
class HarnessConsumptionModel:
    """Structured model of how selected harness input is consumed."""

    pattern: str = "unknown"
    patterns: List[str] = field(default_factory=list)
    data_parameter: str = ""
    size_parameter: str = ""
    first_hops: List[str] = field(default_factory=list)
    selector_expression: str = ""
    magic_bytes: str = ""
    temp_file_api: str = ""
    first_hop_resolution: Dict[str, int] = field(default_factory=dict)
    evidence: List[HarnessConsumptionEvidence] = field(default_factory=list)
    status: str = "unresolved"
    # Extended consumption model fields
    endpoint_scope: str = ""  # buffer|stdin|file|socket|packet|callback|apdu|multi_stage
    carrier_stack: List[str] = field(default_factory=list)  # e.g. ["jnx", "dcm"], ["udp", "ieee1722"]
    required_wrappers: List[str] = field(default_factory=list)
    binary_format: str = ""  # elf|mach-o|pe|raw|wasm|unknown
    architecture_selector: str = ""  # x86|aarch64|riscv|ppc|arm|unknown
    transcript_required: bool = False
    transcript_evidence: List[Dict[str, Any]] = field(default_factory=list)


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
    field_provenance: Dict[str, str] = field(default_factory=dict)
    field_confidence: Dict[str, float] = field(default_factory=dict)
    consumption: HarnessConsumptionModel = field(default_factory=HarnessConsumptionModel)


@dataclass
class HarnessCandidate:
    """One concrete harness entry, identified by source location."""

    candidate_id: str
    binary_names: List[str] = field(default_factory=list)
    source_path: str = ""
    entry_function: str = ""
    line: int = 0
    evidence: List[str] = field(default_factory=list)
    direct_calls: List[str] = field(default_factory=list)
    reachable_symbols: List[str] = field(default_factory=list)
    status: str = "discovered"


@dataclass
class HarnessResolution:
    """Conservative result of relating a task to one harness candidate."""

    status: str = "unresolved"
    selected_candidate_id: str = ""
    selected_binary: str = ""
    reasons: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class HarnessSignal:
    """Structured signal about the task harness or fuzzer target."""

    name: str
    source: str = ""
    evidence: str = ""
    confidence: float = 0.0
