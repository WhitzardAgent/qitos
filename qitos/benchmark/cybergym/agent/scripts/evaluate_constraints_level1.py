#!/usr/bin/env python3
"""Read-only evaluator for Level-1 C/C++ constraint analysis.

The evaluator has an explicit evidence allowlist: tasks.json, description.txt,
and C/C++ files below repo-vul.  It refuses sanitizer output, patches, archives,
and fixed repositories even when a manifest references them.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from cybergym_agent.analysis.constraints.analysis import analyze_constraints
from cybergym_agent.analysis.constraints.models import (
    AnalysisBudget,
    ExtractionRequest,
    SourceUnit,
    hint_from_description,
)


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
FORBIDDEN_NAMES = {"error.txt", "patch.diff", "repo-fix.tar.gz", "fixed.tar.gz"}
FORBIDDEN_PARTS = {"repo-fix", "repo_fixed", "fixed", "sanitizer", "crash-output"}


class EvidencePolicyError(RuntimeError):
    pass


def _reject_forbidden(path: Path) -> None:
    lowered = {part.lower() for part in path.parts}
    if path.name.lower() in FORBIDDEN_NAMES or lowered.intersection(FORBIDDEN_PARTS):
        raise EvidencePolicyError(f"forbidden Level-1 evidence path: {path}")


def _read_json(path: Path, *, exact_allowed: Path) -> Any:
    resolved = path.resolve(strict=True)
    if resolved != exact_allowed.resolve(strict=True):
        raise EvidencePolicyError(f"JSON path is not allowlisted: {resolved}")
    _reject_forbidden(resolved)
    return json.loads(resolved.read_text(encoding="utf-8"))


def _read_description(path: Path, task_root: Path) -> str:
    resolved = path.resolve(strict=True)
    if resolved.name != "description.txt" or resolved.parent != task_root.resolve(strict=True):
        raise EvidencePolicyError(f"description path is not allowlisted: {resolved}")
    _reject_forbidden(resolved)
    return resolved.read_text(encoding="utf-8", errors="replace")


def _read_source(path: Path, repo_vul: Path) -> bytes:
    resolved = path.resolve(strict=True)
    root = repo_vul.resolve(strict=True)
    _reject_forbidden(resolved)
    if resolved.suffix.lower() not in SOURCE_SUFFIXES or not resolved.is_relative_to(root):
        raise EvidencePolicyError(f"source path is not allowlisted below repo-vul: {resolved}")
    return resolved.read_bytes()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _task_dir_name(task_id: str) -> str:
    return task_id.replace(":", "_").replace("/", "_")


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    tasks_path = Path(args.tasks_json)
    workspace_root = Path(args.workspace_root)
    golden_path = Path(args.golden_manifest)
    tasks = _read_json(tasks_path, exact_allowed=tasks_path)
    golden = _read_json(golden_path, exact_allowed=golden_path)
    task_index = {str(item["task_id"]): item for item in tasks}

    language_counts: dict[str, int] = {}
    classified = 0
    family_counts: dict[str, int] = {}
    for task in tasks:
        language = str(task.get("project_language") or "unknown").lower()
        language_counts[language] = language_counts.get(language, 0) + 1
        if language not in {"c", "c++", "cpp"}:
            continue
        hint = hint_from_description(str(task.get("vulnerability_description") or ""))
        if hint.families:
            classified += 1
        for family in hint.families:
            family_counts[family] = family_counts.get(family, 0) + 1

    family_tp = family_fp = family_fn = 0
    source_runs: list[dict[str, Any]] = []
    timings: list[float] = []
    parse_resolved = 0
    candidate_tp = candidate_fp = candidate_fn = 0
    candidate_scored_runs = 0
    unsupported: dict[str, int] = {}

    for entry in golden["tasks"]:
        task_id = str(entry["task_id"])
        task = task_index.get(task_id, {})
        task_root = workspace_root / _task_dir_name(task_id)
        try:
            description = _read_description(task_root / "description.txt", task_root)
        except (OSError, EvidencePolicyError) as exc:
            unsupported["description_unavailable"] = unsupported.get("description_unavailable", 0) + 1
            source_runs.append({"task_id": task_id, "unsupported": str(exc)})
            continue
        predicted = set(hint_from_description(description).families)
        expected = set(entry.get("expected_families", []))
        family_tp += len(predicted & expected)
        family_fp += len(predicted - expected)
        family_fn += len(expected - predicted)

        repo_vul = task_root / "repo-vul"
        source_specs = entry.get("sources", [])
        if not source_specs:
            unsupported["golden_source_unannotated"] = unsupported.get("golden_source_unannotated", 0) + 1
            continue
        for source_spec in source_specs:
            source_path = repo_vul / str(source_spec["path"])
            try:
                source_bytes = _read_source(source_path, repo_vul)
            except (OSError, EvidencePolicyError) as exc:
                unsupported["vulnerable_source_unavailable"] = unsupported.get("vulnerable_source_unavailable", 0) + 1
                source_runs.append({"task_id": task_id, "source": str(source_path), "unsupported": str(exc)})
                continue
            extension = source_path.suffix.lower()
            language = "cpp" if extension in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"} else None
            for sink in source_spec.get("sink_functions", []):
                result = analyze_constraints(ExtractionRequest(
                    source=SourceUnit(
                        source_bytes,
                        path=str(source_path),
                        file_extension=extension,
                        language=language,
                    ),
                    sink_function=str(sink),
                    vulnerability_hint=hint_from_description(description),
                    budget=AnalysisBudget(
                        max_ast_nodes=args.max_ast_nodes,
                        max_candidates=args.max_candidates,
                        max_milliseconds=args.max_milliseconds,
                    ),
                ))
                timings.append(result.stats.elapsed_ms)
                parse_resolved += int(result.sink_resolved)
                actual_origins = {item.origin for item in result.candidates if item.confidence in {"high", "medium"}}
                expected_origins = set(source_spec.get("expected_origins", []))
                allowed_origins = set(source_spec.get("allowed_origins", []))
                if source_spec.get("score_candidates") or expected_origins or allowed_origins:
                    candidate_scored_runs += 1
                    accepted = allowed_origins if "allowed_origins" in source_spec else expected_origins
                    candidate_tp += len(actual_origins & accepted)
                    candidate_fp += len(actual_origins - accepted)
                    candidate_fn += len(expected_origins - actual_origins)
                source_runs.append({
                    "task_id": task_id,
                    "source": str(source_path),
                    "sink": sink,
                    "resolved": result.sink_resolved,
                    "parse_has_error": result.parse_has_error,
                    "candidate_origins": sorted(actual_origins),
                    "elapsed_ms": result.stats.elapsed_ms,
                    "diagnostics": [item.code for item in result.diagnostics],
                })

    c_cpp_tasks = sum(count for language, count in language_counts.items() if language in {"c", "c++", "cpp"})
    precision_denominator = candidate_tp + candidate_fp
    recall_denominator = candidate_tp + candidate_fn
    family_precision_denominator = family_tp + family_fp
    family_recall_denominator = family_tp + family_fn
    return {
        "policy": {
            "allowed": ["tasks.json", "description.txt", "repo-vul/**/*.{c,cc,cpp,cxx,h,hh,hpp,hxx}"],
            "forbidden": sorted(FORBIDDEN_NAMES | FORBIDDEN_PARTS),
        },
        "task_inventory": {
            "total": len(tasks),
            "language_counts": language_counts,
            "c_cpp_tasks": c_cpp_tasks,
            "classified_c_cpp": classified,
            "classification_coverage": classified / c_cpp_tasks if c_cpp_tasks else 0.0,
            "family_counts": family_counts,
        },
        "golden": {
            "tasks": len(golden["tasks"]),
            "family_precision": family_tp / family_precision_denominator if family_precision_denominator else None,
            "family_recall": family_tp / family_recall_denominator if family_recall_denominator else None,
            "source_runs": len(source_runs),
            "sink_resolved": parse_resolved,
            "candidate_scored_runs": candidate_scored_runs,
            "candidate_precision": candidate_tp / precision_denominator if precision_denominator else None,
            "candidate_recall": candidate_tp / recall_denominator if recall_denominator else None,
            "candidate_false_positives": candidate_fp,
            "unsupported": unsupported,
        },
        "performance_ms": {
            "samples": len(timings),
            "p50": statistics.median(timings) if timings else 0.0,
            "p95": _percentile(timings, 0.95),
            "max": max(timings, default=0.0),
        },
        "runs": source_runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-json", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument(
        "--golden-manifest",
        default=str(Path(__file__).resolve().parents[1] / "tests/fixtures/constraint_level1_golden.json"),
    )
    parser.add_argument("--output")
    parser.add_argument("--max-ast-nodes", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=96)
    parser.add_argument("--max-milliseconds", type=int, default=500)
    args = parser.parse_args()
    report = evaluate(args)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
