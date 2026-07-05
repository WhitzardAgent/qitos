"""Stable, JSON-serializable analysis IR and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any


def stable_value(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: stable_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(k): stable_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [stable_value(v) for v in value]
    return value


@dataclass(frozen=True)
class SourceLocation:
    file: str
    start_line: int
    start_column: int = 1
    end_line: int = 0
    end_column: int = 1


@dataclass(frozen=True)
class ExprIR:
    kind: str
    value: Any = None
    children: tuple["ExprIR", ...] = ()
    source_text: str = ""
    location: SourceLocation | None = None

    def substitute(self, bindings: dict[str, "ExprIR"]) -> "ExprIR":
        if self.kind == "identifier" and str(self.value) in bindings:
            return bindings[str(self.value)]
        return ExprIR(self.kind, self.value, tuple(c.substitute(bindings) for c in self.children), self.source_text, self.location)

    def render(self) -> str:
        if self.kind in {"identifier", "constant", "null", "unknown"}:
            return str(self.value if self.value is not None else self.source_text)
        if self.kind in {"field_access", "pointer_field_access"} and self.children:
            return f"{self.children[0].render()}{'->' if self.kind == 'pointer_field_access' else '.'}{self.value}"
        if self.kind == "array_access" and len(self.children) == 2:
            return f"{self.children[0].render()}[{self.children[1].render()}]"
        if self.kind == "binary" and len(self.children) == 2:
            return f"{self.children[0].render()} {self.value} {self.children[1].render()}"
        if self.kind == "unary" and self.children:
            return f"{self.value}{self.children[0].render()}"
        if self.kind == "call":
            return f"{self.value}({', '.join(c.render() for c in self.children)})"
        return self.source_text or str(self.value or "?")


@dataclass(frozen=True)
class Parameter:
    name: str
    type_text: str = ""


@dataclass
class CallCandidate:
    symbol_id: str
    resolution_kind: str
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class ConstraintIR:
    expression: ExprIR
    source_text: str
    normalized_text: str
    polarity: bool
    origin_function: str
    origin_location: SourceLocation
    reason: str
    confidence: float
    role: str = "reachability"
    gate_type: str = "path_gate"
    safe_formula: str = ""
    violation_formula: str = ""
    input_mapping: str = ""


@dataclass
class CallSite:
    callsite_id: str
    caller_id: str
    callee_text: str
    receiver: ExprIR | None
    arguments: list[ExprIR]
    location: SourceLocation
    local_guards: list[ConstraintIR] = field(default_factory=list)
    candidates: list[CallCandidate] = field(default_factory=list)
    resolution_status: str = "unresolved"
    receiver_type: str = ""


@dataclass
class FunctionSymbol:
    symbol_id: str
    name: str
    qualified_name: str
    file: str
    scope: str | None
    parameters: list[Parameter]
    is_static: bool
    language: str
    body_location: SourceLocation
    source_text: str = ""


@dataclass
class DefinitionIR:
    target: str
    expression: ExprIR
    location: SourceLocation
    guards: list[ConstraintIR] = field(default_factory=list)


@dataclass
class RiskSignal:
    """A source-backed operation worth inspecting, not a vulnerability verdict."""

    signal_id: str
    kind: str
    expression: str
    location: SourceLocation
    severity: float
    parameter_dependencies: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class FunctionSummary:
    function_id: str
    parameters: list[str]
    calls: list[CallSite] = field(default_factory=list)
    returns: list[ExprIR] = field(default_factory=list)
    local_definitions: list[DefinitionIR] = field(default_factory=list)
    field_writes: list[DefinitionIR] = field(default_factory=list)
    early_exits: list[SourceLocation] = field(default_factory=list)
    unresolved_nodes: list[dict[str, Any]] = field(default_factory=list)
    risk_signals: list[RiskSignal] = field(default_factory=list)


@dataclass
class CallEdge:
    caller_id: str
    callee_id: str
    callsite_id: str
    bindings: dict[str, ExprIR]
    guards: list[ConstraintIR]
    resolution_kind: str
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class AnalysisPath:
    path_id: str
    symbol_ids: list[str]
    edges: list[CallEdge]
    constraints: list[ConstraintIR]
    score: float
    partial: bool = False
    contradictions: list[str] = field(default_factory=list)


@dataclass
class RankedVulnerabilityPath:
    """Compact ranked path from harness entry to a candidate endpoint."""

    path_id: str
    symbol_ids: list[str]
    endpoint_symbol_id: str
    endpoint_signal_id: str
    endpoint_role: str
    candidate_family: str
    score: float
    score_breakdown: dict[str, float]
    resolution_status: str
    description_ref_ids: list[str] = field(default_factory=list)
    graph_distance_hint: int | None = None
    gaps: list[dict[str, Any]] = field(default_factory=list)
    generation_channels: list[str] = field(default_factory=list)
    role_score: float = 0.0
    event_pair: dict[str, Any] = field(default_factory=dict)
    diversity_key: str = ""
    false_positive_guards: list[str] = field(default_factory=list)
    normalization_warnings: list[dict[str, Any]] = field(default_factory=list)
    loop_detected: bool = False
    paired_endpoint: dict[str, Any] = field(default_factory=dict)
    chain: list[dict[str, Any]] = field(default_factory=list)
    endpoint: dict[str, Any] = field(default_factory=dict)
    next_read: dict[str, Any] = field(default_factory=dict)


@dataclass
class InputByteMapping:
    mapping_id: str
    sink_argument: str
    sink_expression: str
    source_parameter: str = ""
    offset_expression: str = ""
    offset: int | None = None
    width: int | None = None
    endianness: str = "unknown"
    transform: str = ""
    constraint: str = ""
    status: str = "unresolved"
    confidence: float = 0.0
    evidence: list[SourceLocation] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    # v14 recipe extensions
    argument_role: str = ""       # length|index|offset|pointer|state|selector
    value_strategy: str = ""     # oversize|negative|wrap|short_chunk|duplicate_free_sequence
    sink_candidate_id: str = ""
    ranked_path_id: str = ""


@dataclass
class SinkCandidateInput:
    candidate_id: str
    repository_id: str
    file: str
    line: int
    function: str | None = None
    callee: str | None = None
    expression: str | None = None
    category: str | None = None
    reason: str = ""
    agent_confidence: float = 0.5
    evidence_locations: list[SourceLocation] = field(default_factory=list)
    related_cve: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SinkAnalysisBrief:
    brief_id: str
    candidate_id: str
    status: str
    target: dict[str, Any]
    candidate_paths: list[dict[str, Any]]
    key_constraints: list[dict[str, Any]]
    argument_provenance: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    suggested_queries: list[dict[str, Any]]
    confidence: dict[str, float]
    truncation: dict[str, Any]
    full_result_id: str = ""
    context_payload: str = ""
    target_resolution: dict[str, Any] = field(default_factory=dict)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    trigger_conditions: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    input_mappings: list[dict[str, Any]] = field(default_factory=list)
