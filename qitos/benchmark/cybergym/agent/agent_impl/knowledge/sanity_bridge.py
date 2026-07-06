"""Sanity bridge — converts ValidationReport findings to PoCSanityIssue format.

This bridge allows the new five-layer validation system to coexist with
the existing sanity checker during migration.  Pack findings with
strength="authoritative" or "strong" override generic findings;
supporting and heuristic findings only add WARN/INFO.
"""

from __future__ import annotations

from typing import Any

from ..poc.sanity import PoCSanityIssue
from .models import ValidationFinding, ValidationReport, ValidationFinding


def validation_to_sanity_issues(report: ValidationReport) -> list[PoCSanityIssue]:
    """Convert ValidationReport findings to PoCSanityIssue list.

    Strength → severity mapping:
    - authoritative + fail → fail
    - authoritative + warn → warn
    - strong + fail → fail
    - strong + warn → warn
    - supporting + fail → warn (downgraded, never FAIL)
    - supporting + warn → info (downgraded)
    - heuristic + any → info (downgraded)
    """
    issues: list[PoCSanityIssue] = []

    for finding in report.findings:
        severity = _strength_to_severity(finding)
        category = _layer_to_category(finding.layer)

        issues.append(PoCSanityIssue(
            severity=severity,
            category=category,
            message=f"[{finding.validator_id}] {finding.layer}: {finding.verdict}",
            evidence=finding.evidence_ref[:200] if finding.evidence_ref else "",
            repair_hint=finding.repair_actions[0] if finding.repair_actions else "",
        ))

    return issues


def _strength_to_severity(finding: ValidationFinding) -> str:
    """Map validation finding strength + verdict to sanity severity."""
    strength = finding.strength
    verdict = finding.verdict

    if strength == "authoritative":
        if verdict == "fail":
            return "fail"
        elif verdict == "warn":
            return "warn"
        return "info"

    elif strength == "strong":
        if verdict == "fail":
            return "fail"
        elif verdict == "warn":
            return "warn"
        return "info"

    elif strength == "supporting":
        # Supporting findings never create FAIL — downgraded to warn/info
        if verdict == "fail":
            return "warn"
        elif verdict == "warn":
            return "info"
        return "info"

    else:  # heuristic
        return "info"


def _layer_to_category(layer: str) -> str:
    """Map validation layer to sanity issue category."""
    layer_map = {
        "byte_safety": "size",
        "structural_parse": "format",
        "invariant_check": "field",
        "harness_acceptance": "format",
        "mutation_intent": "field",
    }
    return layer_map.get(layer, "format")
