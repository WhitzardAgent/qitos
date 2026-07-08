"""Constraint extraction and analysis for C/C++ source code.

Public API:
    analyze_constraints / analyze_constraint_requests — top-level orchestrators
    ExtractionRequest / SourceUnit / hint_from_description — request models
    BoolExpr / ValueExpr / Compare — IR node types
"""

from .analysis import analyze_constraints, analyze_constraint_requests
from .models import ExtractionRequest, SourceUnit, hint_from_description
from .ir import BoolExpr, ValueExpr, Compare, And, Or, Not

__all__ = [
    "analyze_constraints",
    "analyze_constraint_requests",
    "ExtractionRequest",
    "SourceUnit",
    "hint_from_description",
    "BoolExpr",
    "ValueExpr",
    "Compare",
    "And",
    "Or",
    "Not",
]
