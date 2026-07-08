"""Offline-only evaluation helpers for CyberGym."""

from .error_stack import (
    Candidate,
    ErrorStackReport,
    EvaluationSummary,
    StackFrame,
    build_project_manifest,
    evaluate_candidates,
    normalize_symbol,
    parse_error_file,
    parse_error_text,
)

__all__ = [
    "Candidate",
    "ErrorStackReport",
    "EvaluationSummary",
    "StackFrame",
    "build_project_manifest",
    "evaluate_candidates",
    "normalize_symbol",
    "parse_error_file",
    "parse_error_text",
]
