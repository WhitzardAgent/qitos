"""Investigation-related data models for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class DescriptionAnalysis:
    """LLM-authored interpretation of the task description.

    Every field is a navigation prior until it is verified against source code.
    """

    vuln_type: str = ""
    crash_type_hint: str = ""
    access_mode: str = "unknown"
    memory_region: str = "unknown"
    mechanism_tags: List[str] = field(default_factory=list)
    described_operations: List[str] = field(default_factory=list)
    described_state_transitions: List[str] = field(default_factory=list)
    numeric_facts: List[str] = field(default_factory=list)
    suspect_functions: List[str] = field(default_factory=list)
    suspect_files: List[str] = field(default_factory=list)
    suspect_modules: List[str] = field(default_factory=list)
    suspect_params: List[str] = field(default_factory=list)
    trigger_conditions: List[str] = field(default_factory=list)
    search_hints: List[str] = field(default_factory=list)
    status: str = "pending"
    created_step: int = 0
    last_relevant_step: int = 0


@dataclass
class VerifiedCodeRef:
    """One source-backed match for a description-derived query."""

    query: str
    ref_id: str = ""
    symbol_id: str = ""
    symbol: str = ""
    file: str = ""
    line: int = 0
    match_kind: str = ""
    confidence: float = 0.0
    evidence: str = ""
    status: str = "verified"
    created_step: int = 0
    last_relevant_step: int = 0


@dataclass
class SinkCandidate:
    """A candidate vulnerable function (sink) with confidence scoring."""

    function: str = ""           # function name
    location: str = ""           # file:line
    confidence: float = 0.0      # 0.0-1.0 based on description match
    evidence: str = ""           # why this is considered a sink
    status: str = "candidate"    # candidate / confirmed / eliminated
    source: str = ""             # description / grep / harness_chain / suggested
    candidate_id: str = ""
    repository_id: str = "repo_current"
    file: str = ""
    line: int = 0
    callee: str = ""
    expression: str = ""
    category: str = ""
    reason: str = ""
    evidence_locations: List[Dict[str, Any]] = field(default_factory=list)
    related_cve: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
