#!/usr/bin/env python3
"""Build the vNext offline manifest and evaluate sink candidates.

The script never launches the agent and never exposes ``error.txt`` content to
runtime state.  It consumes already-produced candidate JSON for offline scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from offline_eval.error_stack import (  # noqa: E402
    build_project_manifest,
    dump_json,
    evaluate_candidates,
    load_reports_from_manifest,
)


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _candidate_map(value: Any) -> dict[str, list[Any]]:
    if isinstance(value, dict):
        if "tasks" in value and isinstance(value["tasks"], list):
            value = value["tasks"]
        else:
            return {str(key): list(items or []) for key, items in value.items()}
    result: dict[str, list[Any]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "")
            candidates = item.get("candidates") or item.get("sink_candidates") or []
            if task_id:
                result[task_id] = list(candidates)
    return result


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-json", required=True, help="CyberGym tasks.json")
    parser.add_argument("--error-root", required=True, help="Offline error_txt_only root")
    parser.add_argument("--manifest", help="Existing manifest JSON; generated when omitted")
    parser.add_argument("--manifest-out", help="Write generated manifest JSON")
    parser.add_argument("--candidates-json", help="Task -> ordered candidates JSON")
    parser.add_argument("--report-out", help="Write evaluation report JSON")
    parser.add_argument("--split", choices=("all", "train", "dev", "test"), default="all")
    args = parser.parse_args()

    tasks = _load_json(args.tasks_json)
    if not isinstance(tasks, list):
        parser.error("tasks JSON must contain a list")
    manifest = _load_json(args.manifest) if args.manifest else build_project_manifest(tasks, args.error_root)
    if args.manifest_out:
        dump_json(args.manifest_out, manifest)

    split_counts = Counter(str(item.get("split") or "") for item in manifest)
    project_counts = Counter((str(item.get("split") or ""), str(item.get("project_name") or "")) for item in manifest)
    output: dict[str, Any] = {
        "manifest_tasks": len(manifest),
        "manifest_fingerprint": _fingerprint(manifest),
        "split_tasks": dict(sorted(split_counts.items())),
        "split_projects": {
            split: sum(1 for (item_split, _project) in project_counts if item_split == split)
            for split in ("train", "dev", "test")
        },
        "evaluated_split": args.split,
    }

    if args.candidates_json:
        reports = load_reports_from_manifest(manifest, args.error_root, split=args.split)
        candidates = _candidate_map(_load_json(args.candidates_json))
        output["metrics"] = evaluate_candidates(reports, candidates).to_dict()

    rendered = json.dumps(output, indent=2, sort_keys=True)
    print(rendered)
    if args.report_out:
        dump_json(args.report_out, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
