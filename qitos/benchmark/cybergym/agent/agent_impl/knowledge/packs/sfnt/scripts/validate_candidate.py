#!/usr/bin/env python3
"""Validate an SFNT/Font candidate with the pack validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.agent_impl.knowledge.models import CarrierContract, ExpectedEffect
from cybergym_agent.agent_impl.knowledge.packs.sfnt.validator import (
    validate_sfnt_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an SFNT/Font candidate")
    parser.add_argument("--candidate", required=True, help="Candidate font path")
    parser.add_argument("--raw-marker", default="", help="Raw trigger marker expected in candidate")
    args = parser.parse_args()

    path = Path(args.candidate)
    if not path.is_file():
        print(json.dumps({
            "status": "error",
            "verdict": "fail",
            "reason": "candidate_not_found",
            "candidate": str(path),
        }, sort_keys=True))
        return 1

    mutation_intent = None
    if args.raw_marker:
        mutation_intent = ExpectedEffect(
            effect_id="cli_raw_marker",
            target_expression=f"sfnt.raw_contains:{args.raw_marker}",
            desired_relation="mutation_preserved",
        )

    report = validate_sfnt_candidate(
        str(path),
        CarrierContract(format_id="sfnt"),
        mutation_intent,
    )
    print(json.dumps({
        "status": "success",
        "format": "sfnt",
        "candidate": str(path),
        "verdict": report.overall_verdict,
        "blocks_submit": report.blocks_submit,
        "findings": [
            {
                "validator_id": finding.validator_id,
                "layer": finding.layer,
                "verdict": finding.verdict,
                "strength": finding.strength,
                "evidence_ref": finding.evidence_ref,
                "repair_actions": list(finding.repair_actions),
            }
            for finding in report.findings
        ],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
