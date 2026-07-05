#!/usr/bin/env python3
"""Audit observation/context traces for six-section compliance.

The script is intentionally offline: it reads text/JSON/JSONL trace artifacts
and never contacts the verification server.  It can compare baseline/comparison
directories or audit a single input path.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import median
from typing import Any


EXPECTED = [
    "Mission",
    "Current Assessment",
    "Vulnerability Path",
    "Required Conditions",
    "Experiments",
    "Next Action",
]
ALLOWED = set(EXPECTED)
BAD_PATTERNS = {
    "raw_dict": re.compile(r"\{['\"][A-Za-z0-9_ -]+['\"]\s*:"),
    "html_escape": re.compile(r"&#x27;|&quot;|&lt;|&gt;"),
    "analysis_xml": re.compile(r"</?(?:static_|sink_|code_index|analysis)[A-Za-z0-9_ -]*"),
    "old_section": re.compile(r"##\s+(Foundation|Allowed Tools|Candidate Vulnerability Paths|PoC Byte Layout|Sink Dataflow|Constraint Board|PoC Requirements)\b"),
}
_NEGATIVE_EVIDENCE_RE = re.compile(r"\*\*Negative evidence\*\*|avoid_next|no_crash_unknown|path_reached_no_trigger|carrier_sanity_fail")
_NON_ACTIONABLE_RE = re.compile(r"non-actionable|candidate conditions filtered")
_SUBMIT_NOW_RE = re.compile(r"\*\*SUBMIT NOW\*\*")
_REPLAN_RE = re.compile(r"\*\*Replan recommended\*\*")
_SANITY_FAIL_RE = re.compile(r"\*\*Carrier sanity\*\*: FAIL|CARRIER_SANITY_FAIL")


def _load_texts(path: Path) -> list[str]:
    if path.is_file():
        return _texts_from_file(path)
    texts: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and item.suffix.lower() in {".txt", ".md", ".json", ".jsonl", ".trace"}:
            texts.extend(_texts_from_file(item))
    return texts


def _texts_from_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".jsonl":
        texts = []
        for line in raw.splitlines():
            try:
                value = json.loads(line)
            except Exception:
                continue
            texts.extend(_extract_observation_texts(value))
        return texts or [raw]
    if path.suffix.lower() == ".json":
        try:
            return _extract_observation_texts(json.loads(raw)) or [raw]
        except Exception:
            return [raw]
    return [raw]


def _extract_observation_texts(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, str):
        if "## Mission" in value or "## Current Assessment" in value:
            texts.append(value)
    elif isinstance(value, dict):
        for key in ("observation", "prompt", "content", "text", "runtime_context"):
            if isinstance(value.get(key), str):
                texts.extend(_extract_observation_texts(value[key]))
        for key in ("messages", "events", "steps"):
            if isinstance(value.get(key), list):
                for item in value[key]:
                    texts.extend(_extract_observation_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_extract_observation_texts(item))
    return texts


def _headings(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M)]


def audit_path(path: Path) -> dict[str, Any]:
    texts = _load_texts(path)
    lengths = [len(text) for text in texts]
    token_estimates = [max(1, length // 4) for length in lengths]
    title_violations = 0
    order_violations = 0
    orphan_sections = 0
    provenance_lines = 0
    factual_lines = 0
    bad_counts = {name: 0 for name in BAD_PATTERNS}
    # audit additions
    neg_evidence_count = 0
    non_actionable_count = 0
    submit_now_count = 0
    replan_count = 0
    sanity_fail_count = 0
    for text in texts:
        heads = _headings(text)
        if heads:
            if any(head not in ALLOWED for head in heads):
                title_violations += 1
                orphan_sections += sum(1 for head in heads if head not in ALLOWED)
            expected_order = [head for head in EXPECTED if head in heads]
            if heads != expected_order:
                order_violations += 1
        for name, pattern in BAD_PATTERNS.items():
            if pattern.search(text):
                bad_counts[name] += 1
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("-") and not re.match(r"^\d+\.", stripped):
                continue
            if any(token in stripped.lower() for token in ("source:", "evidence:", "gap:", "recommended", "success:", "pending")):
                factual_lines += 1
                if "[source:" in stripped or "source:" in stripped or "evidence:" in stripped:
                    provenance_lines += 1
        # count negative evidence, non-actionable, submit/replan/sanity
        if _NEGATIVE_EVIDENCE_RE.search(text):
            neg_evidence_count += 1
        if _NON_ACTIONABLE_RE.search(text):
            non_actionable_count += 1
        submit_now_count += len(_SUBMIT_NOW_RE.findall(text))
        replan_count += len(_REPLAN_RE.findall(text))
        if _SANITY_FAIL_RE.search(text):
            sanity_fail_count += 1
    provenance_rate = provenance_lines / factual_lines if factual_lines else 1.0
    def pct(values: list[int], q: float) -> int:
        if not values:
            return 0
        values = sorted(values)
        return values[min(len(values) - 1, int(round((len(values) - 1) * q)))]
    return {
        "path": str(path),
        "observations": len(texts),
        "title_violations": title_violations,
        "order_violations": order_violations,
        "orphan_sections": orphan_sections,
        "bad_patterns": bad_counts,
        "provenance_rate": round(provenance_rate, 4),
        "chars": {"p50": int(median(lengths)) if lengths else 0, "p95": pct(lengths, .95), "max": max(lengths) if lengths else 0},
        "tokens_est": {"p50": int(median(token_estimates)) if token_estimates else 0, "p95": pct(token_estimates, .95), "max": max(token_estimates) if token_estimates else 0},
        "signal_audit": {
            "negative_evidence_observations": neg_evidence_count,
            "non_actionable_count": non_actionable_count,
            "submit_now_count": submit_now_count,
            "replan_count": replan_count,
            "carrier_sanity_fail_count": sanity_fail_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Trace/text/json/jsonl path to audit")
    parser.add_argument("--baseline", help="Baseline trace path")
    parser.add_argument("--comparison", help="Comparison trace path")
    parser.add_argument("--json-out", help="Write report JSON")
    args = parser.parse_args()
    if args.baseline or args.comparison:
        if not args.baseline or not args.comparison:
            parser.error("--baseline and --comparison must be provided together")
        report = {
            "baseline": audit_path(Path(args.baseline)),
            "comparison": audit_path(Path(args.comparison)),
        }
    else:
        if not args.path:
            parser.error("provide a path or --baseline/--comparison")
        report = audit_path(Path(args.path))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out:
        Path(args.json_out).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
