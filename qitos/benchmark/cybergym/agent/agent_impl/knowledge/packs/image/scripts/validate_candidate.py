#!/usr/bin/env python3
"""Validate an Image/TIFF candidate with the pack validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.agent_impl.knowledge.models import CarrierContract, ExpectedEffect
from cybergym_agent.agent_impl.knowledge.packs.image import ImageKnowledgePack


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an Image/TIFF candidate")
    parser.add_argument("--candidate", required=True, help="Candidate image path")
    parser.add_argument("--format", default="image", help="Expected image family, e.g. tiff/png/jpeg/bmp")
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
        prefix = "tiff.raw_contains" if args.format.lower() in {"tiff", "dng", "exif"} else "image.raw_contains"
        mutation_intent = ExpectedEffect(
            effect_id="cli_raw_marker",
            target_expression=f"{prefix}:{args.raw_marker}",
            desired_relation="mutation_preserved",
        )

    report = ImageKnowledgePack().validate(
        str(path),
        CarrierContract(format_id=args.format.lower()),
        mutation_intent,
    )
    print(json.dumps({
        "status": "success",
        "format": args.format.lower(),
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
