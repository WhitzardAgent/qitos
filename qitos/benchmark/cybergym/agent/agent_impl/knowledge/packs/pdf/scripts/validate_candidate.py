#!/usr/bin/env python3
"""Validate a PDF candidate with the lightweight toolbox inspector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.toolbox.formats import pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a PDF candidate")
    parser.add_argument("--candidate", required=True, help="Candidate PDF path")
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

    report = pdf.inspect(str(path))
    valid = (
        bool(report.get("valid_signature"))
        and bool(report.get("has_eof_marker"))
        and bool(report.get("startxref_matches_xref", False))
    )
    issues = []
    if not report.get("valid_signature"):
        issues.append("missing_pdf_header")
    if not report.get("has_eof_marker"):
        issues.append("missing_eof_marker")
    if not report.get("startxref_matches_xref", False):
        issues.append("startxref_mismatch")
    print(json.dumps({
        "status": "success",
        "verdict": "pass" if valid else "fail",
        "format": "pdf",
        "candidate": str(path),
        "issues": issues,
        "inspect": report,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
