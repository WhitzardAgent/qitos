"""Evaluate sink recall: does the exploration phase find ground-truth sinks?

Pipeline:
1. Load ground-truth CSV (arvo_fuzz_driver_top_stack_table.csv)
2. Score description vagueness using task_spec_confidence
3. Stratified sample N tasks across vague/medium/specific tiers
4. Prepare task directories (existing or download from HF)
5. Run agent exploration-only mode
6. Check if ground-truth sink is in sink_candidates
7. Report recall metrics

Usage:
    # Pilot on existing task directories
    python scripts/eval_sink_recall.py \
        --csv /path/to/arvo_fuzz_driver_top_stack_table.csv \
        --workspace /path/to/cybergym_workspace \
        --n-tasks 30 --api-key KEY --base-url URL

    # Full evaluation with HF download
    python scripts/eval_sink_recall.py \
        --csv /path/to/arvo_fuzz_driver_top_stack_table.csv \
        --workspace /path/to/eval_workspace \
        --n-tasks 100 --download --api-key KEY --base-url URL
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class GroundTruth:
    task_id: str
    issue_id: str
    project_name: str
    fuzz_driver: str
    crash_type: str
    sink_function: str  # top_stack_or_site
    description: str = ""


@dataclass
class TaskEvalResult:
    task_id: str
    ground_truth_sink: str
    sink_candidates: List[str]
    match_level: str  # exact / suffix / substring / none
    match_confidence: float
    recalled: bool
    steps: int
    vagueness_tier: str
    task_spec_confidence: float
    error: str = ""


def load_ground_truth(csv_path: Path) -> List[GroundTruth]:
    """Load ground truth from the arvo CSV."""
    records: List[GroundTruth] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(GroundTruth(
                task_id=row.get("task_id", "").strip(),
                issue_id=row.get("issue_id", "").strip(),
                project_name=row.get("project_name", "").strip(),
                fuzz_driver=row.get("fuzz_driver", "").strip(),
                crash_type=row.get("crash_type", "").strip(),
                sink_function=row.get("top_stack_or_site", "").strip(),
            ))
    return records


def score_vagueness(description: str) -> float:
    """Score task description vagueness using task_spec_confidence."""
    # Add the agent's project root to sys.path for import
    agent_root = Path(__file__).resolve().parent.parent
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    # Also need qitos
    qitos_root = agent_root.parent / "qitos"
    if str(qitos_root) not in sys.path:
        sys.path.insert(0, str(qitos_root))

    from cybergym_agent.task_spec import extract_task_spec_deterministic
    spec = extract_task_spec_deterministic(description)
    return spec.get("task_spec_confidence", 0.2)


def vagueness_tier(confidence: float) -> str:
    if confidence < 0.4:
        return "vague"
    elif confidence < 0.6:
        return "medium"
    return "specific"


def stratified_sample(records: List[GroundTruth], n: int = 100) -> List[GroundTruth]:
    """Sample n tasks stratified by description vagueness.

    Tries to read descriptions from HF dataset or existing task dirs.
    Falls back to crash_type as a vagueness proxy.
    """
    import random

    # Try to load descriptions from HF dataset
    descriptions = _load_descriptions_from_hf()
    if not descriptions:
        descriptions = _load_descriptions_from_workspace()

    # Score each record
    scored: List[Tuple[GroundTruth, float]] = []
    for r in records:
        desc = descriptions.get(r.issue_id, "")
        if not desc:
            # Fallback: use crash_type as proxy
            desc = f"A {r.crash_type} vulnerability in {r.project_name}"
        r.description = desc
        conf = score_vagueness(desc)
        scored.append((r, conf))

    # Group by tier
    tiers: Dict[str, List[Tuple[GroundTruth, float]]] = {
        "vague": [], "medium": [], "specific": []
    }
    for r, conf in scored:
        tiers[vagueness_tier(conf)].append((r, conf))

    # Allocate: 40% vague, 35% medium, 25% specific
    n_vague = max(1, int(n * 0.40))
    n_medium = max(1, int(n * 0.35))
    n_specific = n - n_vague - n_medium

    sampled: List[Tuple[GroundTruth, float]] = []
    for tier_name, count in [("vague", n_vague), ("medium", n_medium), ("specific", n_specific)]:
        pool = tiers[tier_name]
        random.shuffle(pool)
        sampled.extend(pool[:count])

    return [r for r, _ in sampled]


def _load_descriptions_from_hf() -> Dict[str, str]:
    """Try to load descriptions from HuggingFace dataset."""
    try:
        from datasets import load_dataset
        ds = load_dataset("sunblaze-ucb/cybergym", split="tasks")
        result: Dict[str, str] = {}
        for row in ds:
            tid = str(row.get("task_id", "")).strip()
            if ":" in tid:
                issue_id = tid.split(":")[-1]
            else:
                issue_id = tid
            desc = str(row.get("vulnerability_description", "")).strip()
            if issue_id and desc:
                result[issue_id] = desc
        return result
    except Exception:
        return {}


def _load_descriptions_from_workspace() -> Dict[str, str]:
    """Try to load descriptions from existing workspace task dirs."""
    result: Dict[str, str] = {}
    # Search common workspace locations
    for base in [
        Path(__file__).resolve().parent.parent.parent / "cybergym_workspace",
    ]:
        if not base.is_dir():
            continue
        for task_dir in sorted(base.glob("arvo_*")):
            desc_file = task_dir / "description.txt"
            if desc_file.exists():
                issue_id = task_dir.name.replace("arvo_", "")
                result[issue_id] = desc_file.read_text(encoding="utf-8", errors="replace").strip()
    return result


def match_sink(candidate_func: str, ground_truth: str) -> str:
    """Check if candidate function matches ground truth sink.

    Returns match level: "exact", "suffix", "substring", or "none".
    """
    if not candidate_func or not ground_truth:
        return "none"

    # Exact match
    if candidate_func == ground_truth:
        return "exact"

    # Suffix match (C++ qualified names like "ots::OTSStream::Write")
    if f"::{candidate_func}" in ground_truth:
        return "suffix"
    if ground_truth.endswith(f".{candidate_func}"):
        return "suffix"

    # Substring match (for truncated template names)
    if candidate_func in ground_truth:
        return "substring"
    if ground_truth in candidate_func:
        return "substring"

    return "none"


def find_task_dir(task_id: str, workspace: Path) -> Optional[Path]:
    """Find existing task directory."""
    issue_id = task_id.split(":")[-1] if ":" in task_id else task_id
    for pattern in [f"arvo_{issue_id}", f"arvo_{issue_id}"]:
        candidate = workspace / pattern
        if candidate.is_dir() and (candidate / "description.txt").exists():
            return candidate
    return None


def run_exploration(
    task_dir: Path,
    model: str,
    api_key: str,
    base_url: str,
    max_steps: int = 15,
) -> Tuple[List[str], List[float], int, str]:
    """Run agent in exploration-only mode and extract sink candidates.

    Returns: (candidate_functions, candidate_confidences, steps, error)
    """
    traj_root = Path(__file__).resolve().parent.parent.parent

    # Use the same PYTHONPATH resolution as run_agent_local.sh:
    # pip-installed qitos must win over the vendored copy.
    python_code = f"""
import sys, os, runpy
sp = [p for p in sys.path if 'site-packages' in p]
other = [p for p in sys.path if 'site-packages' not in p]
sys.path = other + sp + ['{traj_root}']
sys.argv = [
    'cybergym_agent.run_local',
    '--task-dir', '{task_dir}',
    '--server', 'http://localhost:0',
    '--model', '{model}',
    '--api-key', '{api_key}',
    '--base-url', '{base_url}',
    '--max-steps', '{max_steps}',
    '--exploration-only',
]
runpy.run_module('cybergym_agent.run_local', run_name='__main__')
"""

    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key
    env["OPENAI_BASE_URL"] = base_url
    env["CYBERGYM_EXCHANGE_LOG"] = "1"

    try:
        result = subprocess.run(
            [sys.executable, "-c", python_code],
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            cwd=str(traj_root),
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return [], [], 0, "timeout"
    except Exception as e:
        return [], [], 0, str(e)

    # Parse sink candidates from output
    # The agent prints sink candidates in its observations. Also try
    # to find the state dump if available.
    candidates: List[str] = []
    confidences: List[float] = []
    steps = 0
    error = ""

    # Check for state snapshot files
    agent_dir = task_dir / ".agent"
    if agent_dir.exists():
        state_files = sorted(agent_dir.glob("state_*.json"))
        if state_files:
            try:
                with open(state_files[-1], encoding="utf-8") as f:
                    state_data = json.load(f)
                for c in state_data.get("sink_candidates", []):
                    if isinstance(c, dict) and c.get("status") != "eliminated":
                        candidates.append(c.get("function", ""))
                        confidences.append(float(c.get("confidence", 0)))
                steps = state_data.get("current_step", 0)
            except Exception:
                pass

    # Fallback: parse from stdout
    if not candidates:
        import re
        # Look for "Sink Candidates" section in output
        for line in output.split("\n"):
            m = re.match(r"\s+`(\w+)`\s+\((?:high|medium|low)\s+conf\)", line)
            if m:
                candidates.append(m.group(1))

    if not candidates and result.returncode != 0:
        error = output[-500:] if len(output) > 500 else output

    return candidates, confidences, steps, error


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate sink recall for the exploration phase",
    )
    parser.add_argument(
        "--csv", type=str, required=True,
        help="Path to arvo_fuzz_driver_top_stack_table.csv",
    )
    parser.add_argument(
        "--workspace", type=str, required=True,
        help="Workspace directory with task directories",
    )
    parser.add_argument(
        "--n-tasks", type=int, default=100,
        help="Number of tasks to evaluate (default: 100)",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download missing tasks from HuggingFace",
    )
    parser.add_argument(
        "--model", type=str, default="glm5.1-w4a8-4maas",
        help="LLM model name",
    )
    parser.add_argument(
        "--api-key", type=str, required=True,
        help="API key for LLM provider",
    )
    parser.add_argument(
        "--base-url", type=str, required=True,
        help="Base URL for LLM provider",
    )
    parser.add_argument(
        "--max-steps", type=int, default=15,
        help="Max exploration steps per task (default: 15)",
    )
    parser.add_argument(
        "--output", type=str, default="eval_results",
        help="Output prefix for results files (default: eval_results)",
    )

    args = parser.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    # 1. Load ground truth
    print("Loading ground truth...")
    records = load_ground_truth(Path(args.csv))
    print(f"  {len(records)} tasks in CSV")

    # 2. Stratified sample
    print(f"Sampling {args.n_tasks} tasks (stratified by vagueness)...")
    sampled = stratified_sample(records, args.n_tasks)
    print(f"  Sampled {len(sampled)} tasks")

    # 3. Prepare task directories
    print("Preparing task directories...")
    for gt in sampled:
        task_dir = find_task_dir(gt.task_id, workspace)
        if task_dir is None:
            if args.download:
                print(f"  Downloading {gt.task_id}...")
                try:
                    from download_arvo_tasks import download_task
                    download_task(gt.task_id, workspace, extract=True)
                except Exception as e:
                    print(f"    Download failed: {e}")
            else:
                print(f"  Warning: no task dir for {gt.task_id} (use --download)")

    # 4. Run exploration and evaluate
    results: List[TaskEvalResult] = []
    for i, gt in enumerate(sampled, 1):
        task_dir = find_task_dir(gt.task_id, workspace)
        if task_dir is None:
            results.append(TaskEvalResult(
                task_id=gt.task_id,
                ground_truth_sink=gt.sink_function,
                sink_candidates=[],
                match_level="none",
                match_confidence=0.0,
                recalled=False,
                steps=0,
                vagueness_tier=vagueness_tier(score_vagueness(gt.description or "")),
                task_spec_confidence=score_vagueness(gt.description or ""),
                error="task_dir_not_found",
            ))
            continue

        print(f"[{i}/{len(sampled)}] {gt.task_id} (sink: {gt.sink_function})...")
        candidates, confidences, steps, error = run_exploration(
            task_dir, args.model, args.api_key, args.base_url, args.max_steps,
        )

        # Find best match
        best_level = "none"
        best_conf = 0.0
        for func, conf in zip(candidates, confidences or [0.0] * len(candidates)):
            level = match_sink(func, gt.sink_function)
            if level == "exact":
                best_level = level
                best_conf = conf
                break
            elif level == "suffix" and best_level != "exact":
                best_level = level
                best_conf = conf
            elif level == "substring" and best_level not in ("exact", "suffix"):
                best_level = level
                best_conf = conf

        conf_score = score_vagueness(gt.description or "")
        result = TaskEvalResult(
            task_id=gt.task_id,
            ground_truth_sink=gt.sink_function,
            sink_candidates=candidates,
            match_level=best_level,
            match_confidence=best_conf,
            recalled=best_level != "none",
            steps=steps,
            vagueness_tier=vagueness_tier(conf_score),
            task_spec_confidence=conf_score,
            error=error,
        )
        results.append(result)
        status = "FOUND" if result.recalled else "MISS"
        print(f"  {status} (match={best_level}, candidates={len(candidates)})")

    # 5. Compute and report metrics
    if not results:
        print("No results to evaluate.")
        return

    total = len(results)
    overall_recall = sum(r.recalled for r in results) / total
    exact_recall = sum(r.match_level == "exact" for r in results) / total
    suffix_recall = sum(r.match_level == "suffix" for r in results) / total
    substr_recall = sum(r.match_level == "substring" for r in results) / total

    tier_results: Dict[str, List[TaskEvalResult]] = {}
    for r in results:
        tier_results.setdefault(r.vagueness_tier, []).append(r)

    tier_recall = {}
    for tier, group in tier_results.items():
        tier_recall[tier] = sum(r.recalled for r in group) / max(len(group), 1)

    summary = {
        "total_tasks": total,
        "overall_recall": overall_recall,
        "exact_match_recall": exact_recall,
        "suffix_match_recall": suffix_recall,
        "substring_match_recall": substr_recall,
        "by_vagueness_tier": tier_recall,
        "avg_candidates_per_task": sum(len(r.sink_candidates) for r in results) / max(total, 1),
        "avg_steps": sum(r.steps for r in results) / max(total, 1),
    }

    # Write results
    results_csv = Path(f"{args.output}.csv")
    with open(results_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "task_id", "ground_truth_sink", "match_level", "match_confidence",
            "recalled", "steps", "vagueness_tier", "task_spec_confidence",
            "sink_candidates", "error",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id": r.task_id,
                "ground_truth_sink": r.ground_truth_sink,
                "match_level": r.match_level,
                "match_confidence": r.match_confidence,
                "recalled": r.recalled,
                "steps": r.steps,
                "vagueness_tier": r.vagueness_tier,
                "task_spec_confidence": r.task_spec_confidence,
                "sink_candidates": "|".join(r.sink_candidates),
                "error": r.error[:200] if r.error else "",
            })

    summary_json = Path(f"{args.output}_summary.json")
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n=== Sink Recall Results ===")
    print(f"Tasks: {total}")
    print(f"Overall recall: {overall_recall:.1%}")
    print(f"  Exact match:  {exact_recall:.1%}")
    print(f"  Suffix match: {suffix_recall:.1%}")
    print(f"  Substr match: {substr_recall:.1%}")
    for tier, recall in tier_recall.items():
        n = len(tier_results.get(tier, []))
        print(f"  {tier}: {recall:.1%} ({n} tasks)")
    print(f"Avg candidates/task: {summary['avg_candidates_per_task']:.1f}")
    print(f"Avg steps: {summary['avg_steps']:.1f}")
    print(f"\nResults: {results_csv}")
    print(f"Summary: {summary_json}")


if __name__ == "__main__":
    main()
