"""Public request/result models for Level-1 source constraint analysis."""

from __future__ import annotations

import re
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .ast import SourceSpan
from .ir import BoolExpr, expr_to_dict


HINT_FAMILIES = frozenset({
    "bounds_read",
    "bounds_write",
    "integer_arithmetic",
    "initialization",
    "lifetime",
    "null_return",
    "resource_progress",
    "state_semantic",
    "format_routing",
})


@dataclass(frozen=True)
class VulnerabilityHint:
    """Description-derived navigation hints; never source evidence themselves."""

    families: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()
    raw_description: str = ""


@dataclass(frozen=True)
class SourceUnit:
    text: str | bytes
    path: str = ""
    language: Optional[str] = None
    file_extension: str = ".c"
    line_offset: int = 0
    completeness: str = "full_file"  # full_file | full_function | snippet


@dataclass(frozen=True)
class AnalysisBudget:
    max_ast_nodes: int = 50_000
    max_target_callsites: int = 32
    max_constraints_per_path: int = 16
    max_sink_candidates: int = 64
    max_related_sources: int = 32
    max_candidates: int = 96
    max_milliseconds: int = 500


@dataclass(frozen=True)
class MemoryApiModel:
    destination_arg: Optional[int]
    source_arg: Optional[int]
    length_arg: int
    terminates_destination: bool = False


@dataclass(frozen=True)
class ApiModelConfig:
    """Source-analysis API semantics; names are matched exactly by leaf name."""

    noreturn_functions: frozenset[str] = frozenset()
    nullable_returns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allocation_functions: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    deallocation_functions: Mapping[str, int] = field(default_factory=dict)
    memory_functions: Mapping[str, MemoryApiModel] = field(default_factory=dict)
    failure_returns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def default_api_models() -> ApiModelConfig:
    return ApiModelConfig(
        noreturn_functions=frozenset({"abort", "exit", "_Exit", "quick_exit", "__builtin_trap", "terminate"}),
        nullable_returns={
            "malloc": ("NULL",), "calloc": ("NULL",), "realloc": ("NULL",),
            "aligned_alloc": ("NULL",), "fopen": ("NULL",),
        },
        allocation_functions={
            "malloc": (0,), "calloc": (0, 1), "realloc": (1,), "aligned_alloc": (1,),
        },
        deallocation_functions={"free": 0},
        memory_functions={
            "memcpy": MemoryApiModel(0, 1, 2),
            "memmove": MemoryApiModel(0, 1, 2),
            "memset": MemoryApiModel(0, None, 2),
            "strncpy": MemoryApiModel(0, 1, 2),
            "strncat": MemoryApiModel(0, 1, 2, terminates_destination=True),
            "snprintf": MemoryApiModel(0, None, 1, terminates_destination=True),
        },
        failure_returns={"fread": ("0",), "fwrite": ("0",), "read": ("-1",), "write": ("-1",)},
    )


class BudgetExceeded(RuntimeError):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


@dataclass
class BudgetContext:
    budget: AnalysisBudget
    started_at: float = field(default_factory=time.perf_counter)
    candidates: int = 0
    callsites: int = 0
    related_sources: int = 0
    exhausted_stages: list[str] = field(default_factory=list)

    def checkpoint(self, stage: str) -> None:
        elapsed_ms = (time.perf_counter() - self.started_at) * 1000
        if elapsed_ms > self.budget.max_milliseconds:
            self.exhausted_stages.append(stage)
            raise BudgetExceeded(stage, f"analysis deadline {self.budget.max_milliseconds}ms exhausted")

    def consume_candidate(self, stage: str) -> None:
        self.checkpoint(stage)
        if self.candidates >= self.budget.max_candidates:
            self.exhausted_stages.append(stage)
            raise BudgetExceeded(stage, f"candidate budget {self.budget.max_candidates} exhausted")
        self.candidates += 1


@dataclass(frozen=True)
class ExtractionRequest:
    source: SourceUnit
    caller_function: str = ""
    target_function: str = ""
    target_callsite: Any = None
    sink_function: str = ""
    sink_span: Any = None
    vulnerability_hint: VulnerabilityHint = field(default_factory=VulnerabilityHint)
    related_sources: Mapping[str, SourceUnit] = field(default_factory=dict)
    noreturn_functions: frozenset[str] = frozenset()
    api_models: ApiModelConfig = field(default_factory=default_api_models)
    budget: AnalysisBudget = field(default_factory=AnalysisBudget)


@dataclass(frozen=True)
class ExtractionDiagnostic:
    code: str
    message: str
    severity: str = "info"
    source_span: Optional[SourceSpan] = None

    def to_dict(self) -> dict[str, Any]:
        return _stable_value(self)


@dataclass
class ConstraintCandidate:
    """One source-backed atomic constraint or conservative hazard."""

    gate_type: str
    description: str
    required_condition: str
    polarity: str
    confidence: str
    source: str
    node_function: str = ""
    normalized_formula: str = ""
    raw_condition: str = ""
    source_span: Optional[SourceSpan] = None
    start_line: int = 0
    end_line: int = 0
    enclosing_function: str = ""
    target_function: str = ""
    target_call_span: Optional[SourceSpan] = None
    origin: str = "unknown_control_condition"
    control_origin: str = ""
    confidence_score: float = 0.0
    structured_formula: dict[str, Any] = field(default_factory=dict)
    format_details: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    role: str = "reachability"
    path_id: str = ""
    sink_span: Optional[SourceSpan] = None
    access_mode: str = ""
    required_formula: str = ""
    safe_formula: str = ""
    violation_formula: str = ""
    promotable: bool = False
    confidence_reasons: list[str] = field(default_factory=list)
    symbol_dependencies: list[str] = field(default_factory=list)
    semantic_tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        formula = self.required_formula or self.normalized_formula
        if formula:
            self.required_formula = formula
            self.normalized_formula = formula
            self.required_condition = formula
            self.polarity = "satisfy"
        if self.source_span is not None:
            self.start_line = self.start_line or self.source_span.start_line
            self.end_line = self.end_line or self.source_span.end_line
        if not self.enclosing_function:
            self.enclosing_function = self.node_function
        if not self.node_function:
            self.node_function = self.enclosing_function

    def to_dict(self) -> dict[str, Any]:
        return _stable_value(self)


@dataclass(frozen=True)
class ConstraintPath:
    path_id: str
    anchor_span: SourceSpan
    required_formula: str
    candidate_indexes: tuple[int, ...]
    target_function: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _stable_value(self)


@dataclass
class ExtractionStats:
    ast_nodes_visited: int = 0
    target_callsites: int = 0
    sink_anchors: int = 0
    candidates: int = 0
    paths: int = 0
    truncated: bool = False

    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return _stable_value(self)


@dataclass
class ExtractionResult:
    candidates: list[ConstraintCandidate] = field(default_factory=list)
    paths: list[ConstraintPath] = field(default_factory=list)
    diagnostics: list[ExtractionDiagnostic] = field(default_factory=list)
    target_resolved: bool = False
    sink_resolved: bool = False
    parse_language: str = ""
    parse_has_error: bool = False
    stats: ExtractionStats = field(default_factory=ExtractionStats)
    unsupported_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _stable_value(self)


def stable_path_id(
    kind: str,
    source_path: str,
    caller: str,
    target: str,
    span: SourceSpan,
) -> str:
    evidence = f"{source_path}\0{caller}\0{target}\0{span.start_byte}\0{span.end_byte}"
    digest = hashlib.blake2s(evidence.encode("utf-8", errors="replace"), digest_size=6).hexdigest()
    return f"{kind}:{caller or '-'}:{target or '-'}:{span.start_line}:{span.start_column}:{digest}"


def _stable_value(value: Any) -> Any:
    if isinstance(value, SourceSpan):
        return value.as_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {name: _stable_value(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_stable_value(item) for item in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def hint_from_description(description: str) -> VulnerabilityHint:
    """Classify navigation hints without turning prose into constraints."""
    text = str(description or "")
    lowered = text.lower()
    families: list[str] = []
    patterns = {
        "bounds_read": r"out[- ]of[- ]bounds\s+read|(?:heap|stack|global)?[- ]?buffer[- ](?:overflow|overrun)\s+read|\boob[- ]?read\b|invalid read|over[- ]read|read past|access past|incrementing past|indices? (?:are )?not bounds checked|strnstr|cwe-?125",
        "bounds_write": r"out[- ]of[- ]bounds\s+write|\boob[- ]?write\b|(?:heap|stack|global)?[- ]?buffer[- ](?:overflow|overrun|underflow)(?!\s+read)|invalid write|overwrite|output size (?:is )?not checked|cwe-?(?:787|120|121|122)",
        "integer_arithmetic": r"\binteger\b|integer[- ](?:overflow|underflow)|arithmetic[- ](?:overflow|underflow)|signedness|wrap[- ]?around|truncat|narrowing|division by zero|divide by zero|illegal shift|shift count|cwe-?(?:190|191|681)",
        "initialization": r"uninitiali[sz]ed|use[- ]of[- ]uninitiali[sz]ed|not initialize|pad bits|zero[- ]fill|cwe-?457",
        "lifetime": r"use[- ]after|\buaf\b|double[- ]free|invalid[- ]free|already been freed|lifetime|use[- ]after[- ]scope|dangling|cwe-?(?:416|415)",
        "null_return": r"null(?: pointer)?(?: dereference)?|return value|error code|allocation fail|malloc fail|cwe-?476",
        "resource_progress": r"infinite loop|non[- ]terminat|hang|timeout|excessive memory|memory exhaustion|resource|out[- ]of[- ]memory|oom|cwe-?(?:400|835)",
        "format_routing": r"parser|parsing|parse|decode|format|header|packet|frame|tag|tlv|codec|coder",
        "state_semantic": r"state|inconsistent|duplicate|type confusion|invalid type|invalid digit|discriminator|not verified|without validat|different node count|not properly handled|incorrect processing|assum(?:e|ed|ption)|flag|mode|unsupported|cwe-?843",
    }
    for family, pattern in patterns.items():
        if re.search(pattern, lowered):
            families.append(family)

    symbols: list[str] = []
    symbol_patterns = (
        r"`([A-Za-z_~][A-Za-z0-9_:~]*)`",
        r"\b([A-Za-z_~][A-Za-z0-9_:~]*)\(\)",
        r"\b(?:function|method)\s+([A-Za-z_~][A-Za-z0-9_:~]*)",
        r"\b(?:in|within)\s+(?:the\s+)?([A-Za-z_~][A-Za-z0-9_:~]*)\s+function\b",
    )
    for pattern in symbol_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = match.group(1)
            if value.lower() not in {"the", "this", "code", "parser", "due", "in", "allows", "lacks"} and value not in symbols:
                symbols.append(value)

    source_files = tuple(dict.fromkeys(re.findall(
        r"\b[\w./-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx)\b",
        text,
        re.IGNORECASE,
    )))
    return VulnerabilityHint(
        families=tuple(families),
        symbols=tuple(symbols[:16]),
        source_files=source_files[:16],
        raw_description=text,
    )


def candidate_from_expr(
    expr: BoolExpr,
    **kwargs: Any,
) -> ConstraintCandidate:
    formula = expr.render()
    return ConstraintCandidate(
        required_formula=formula,
        normalized_formula=formula,
        required_condition=formula,
        structured_formula=expr_to_dict(expr),
        polarity="satisfy",
        **kwargs,
    )


__all__ = [
    "AnalysisBudget",
    "ApiModelConfig",
    "BudgetContext",
    "BudgetExceeded",
    "ConstraintCandidate",
    "ConstraintPath",
    "ExtractionDiagnostic",
    "ExtractionRequest",
    "ExtractionResult",
    "ExtractionStats",
    "HINT_FAMILIES",
    "MemoryApiModel",
    "SourceUnit",
    "VulnerabilityHint",
    "candidate_from_expr",
    "default_api_models",
    "hint_from_description",
    "stable_path_id",
]
