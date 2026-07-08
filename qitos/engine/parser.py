"""Parser protocol definitions for Engine integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, Generic, List, Optional, Protocol, TypeVar

from ..core.decision import Decision


ActionT = TypeVar("ActionT")


class Parser(Protocol, Generic[ActionT]):
    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[ActionT]:
        """Parse raw output into a validated Decision."""


class BaseParser(ABC, Generic[ActionT]):
    contract_id = "base_parser_v1"

    @abstractmethod
    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[ActionT]:
        """Parse raw output into Decision."""


@dataclass
class ParserDiagnostic:
    parser: str
    contract: str
    severity: str
    code: str
    summary: str
    details: str = ""
    repair_instruction: str = ""
    expected_shape: str = ""
    issue_path: Optional[str] = None
    extraction_mode: str = ""
    protocol: str = ""
    selected_parser: str = ""
    fallback_used: bool = False
    parser_attempts: Optional[List[Dict[str, Any]]] = None
    raw_output_preview: str = ""
    raw_output_chars: int = 0
    salvage_applied: bool = False
    salvage_summary: str = ""
    step_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["parser_attempts"] = list(self.parser_attempts or [])
        return payload


def parser_name(parser: Any) -> str:
    if isinstance(parser, str):
        return parser
    if parser is None:
        return "unknown_parser"
    return parser.__class__.__name__


def parser_contract(parser: Any) -> str:
    if isinstance(parser, str):
        return parser
    if parser is None:
        return "unknown_parser_v1"
    return str(getattr(parser, "contract_id", parser.__class__.__name__.lower()))


def parser_raw_preview(raw_output: Any, limit: int = 50000) -> str:
    text = str(raw_output or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "... (truncated)"


def build_parser_diagnostics(
    *,
    parser: Any,
    severity: str,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    repair_instruction: str = "",
    expected_shape: str = "",
    issue_path: Optional[str] = None,
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    step_id: Optional[int] = None,
) -> Dict[str, Any]:
    diag = ParserDiagnostic(
        parser=parser_name(parser),
        contract=parser_contract(parser),
        severity=str(severity),
        code=str(code),
        summary=str(summary).strip(),
        details=str(details).strip(),
        repair_instruction=str(repair_instruction).strip(),
        expected_shape=str(expected_shape).strip(),
        issue_path=(
            str(issue_path).strip()
            if isinstance(issue_path, str) and issue_path.strip()
            else None
        ),
        extraction_mode=str(extraction_mode).strip(),
        protocol=str(protocol).strip(),
        selected_parser=str(selected_parser).strip(),
        fallback_used=bool(fallback_used),
        parser_attempts=list(parser_attempts or []),
        raw_output_preview=parser_raw_preview(raw_output),
        raw_output_chars=len(str(raw_output or "")),
        salvage_applied=bool(salvage_applied),
        salvage_summary=str(salvage_summary).strip(),
        step_id=step_id,
    )
    return diag.to_dict()


def parser_wait_decision(
    *,
    parser: Any,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    repair_instruction: str = "",
    expected_shape: str = "",
    issue_path: Optional[str] = None,
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    rationale: Optional[str] = None,
    step_id: Optional[int] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Decision[Any]:
    diagnostics = build_parser_diagnostics(
        parser=parser,
        severity="error",
        code=code,
        summary=summary,
        raw_output=raw_output,
        details=details,
        repair_instruction=repair_instruction,
        expected_shape=expected_shape,
        issue_path=issue_path,
        extraction_mode=extraction_mode,
        protocol=protocol,
        selected_parser=selected_parser,
        fallback_used=fallback_used,
        parser_attempts=parser_attempts,
        salvage_applied=salvage_applied,
        salvage_summary=salvage_summary,
        step_id=step_id,
    )
    meta = dict(extra_meta or {})
    meta["parser_error"] = True
    meta["parser_feedback"] = (
        diagnostics["repair_instruction"] or diagnostics["summary"]
    )
    meta["parser_diagnostics"] = diagnostics
    return Decision.wait(rationale=rationale or diagnostics["summary"], meta=meta)


def attach_parser_warning(
    meta: Optional[Dict[str, Any]],
    *,
    parser: Any,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    expected_shape: str = "",
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    step_id: Optional[int] = None,
) -> Dict[str, Any]:
    merged = dict(meta or {})
    diagnostics = build_parser_diagnostics(
        parser=parser,
        severity="warning",
        code=code,
        summary=summary,
        raw_output=raw_output,
        details=details,
        repair_instruction="",
        expected_shape=expected_shape,
        extraction_mode=extraction_mode,
        protocol=protocol,
        selected_parser=selected_parser,
        fallback_used=fallback_used,
        parser_attempts=parser_attempts,
        salvage_applied=salvage_applied,
        salvage_summary=salvage_summary,
        step_id=step_id,
    )
    merged["parser_warning"] = diagnostics["salvage_summary"] or diagnostics["summary"]
    merged["parser_diagnostics"] = diagnostics
    return merged


def normalize_parser_diagnostics(
    meta: Optional[Dict[str, Any]],
    *,
    parser: Any,
    raw_output: Any = None,
    step_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(meta, dict):
        return None
    diagnostics = meta.get("parser_diagnostics")
    if isinstance(diagnostics, dict):
        normalized = dict(diagnostics)
        normalized.setdefault("parser", parser_name(parser))
        normalized.setdefault("contract", parser_contract(parser))
        normalized.setdefault("raw_output_preview", parser_raw_preview(raw_output))
        normalized.setdefault("raw_output_chars", len(str(raw_output or "")))
        normalized.setdefault("step_id", step_id)
        normalized.setdefault(
            "severity", "error" if meta.get("parser_error") else "warning"
        )
        normalized.setdefault("extraction_mode", "")
        normalized.setdefault("protocol", "")
        normalized.setdefault("selected_parser", parser_name(parser))
        normalized.setdefault("fallback_used", False)
        normalized.setdefault("parser_attempts", [])
        return normalized
    if not meta.get("parser_error") and not meta.get("parser_warning"):
        return None
    severity = "error" if meta.get("parser_error") else "warning"
    feedback = str(
        meta.get("parser_feedback") or meta.get("parser_warning") or ""
    ).strip()
    return build_parser_diagnostics(
        parser=parser,
        severity=severity,
        code="legacy_parser_feedback",
        summary=feedback
        or ("Parser error." if severity == "error" else "Parser warning."),
        raw_output=raw_output,
        details="Parser returned legacy parser_error/parser_warning fields without structured diagnostics.",
        repair_instruction=feedback if severity == "error" else "",
        expected_shape="See the configured parser contract for required output fields.",
        step_id=step_id,
    )


__all__ = [
    "Parser",
    "BaseParser",
    "ParserDiagnostic",
    "parser_name",
    "parser_contract",
    "parser_raw_preview",
    "build_parser_diagnostics",
    "parser_wait_decision",
    "attach_parser_warning",
    "normalize_parser_diagnostics",
]
