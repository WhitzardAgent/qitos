#!/usr/bin/env python3
"""Evaluate KnowledgePack/toolbox/sanity behavior against CyberGym GT PoCs.

This is offline evaluation only.  Ground-truth PoCs must not be copied into
runtime task workspaces, prompts, or seed pools.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
for path in (PARENT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from offline_eval.pack_ground_truth import (  # noqa: E402
    evaluate_pack_ground_truth,
    write_jsonl,
    write_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-json", default="../cybergym_full_tasks/tasks.json")
    parser.add_argument("--format-ground-truth", default="../cybergym_full_tasks/task_format_ground_truth.json")
    parser.add_argument("--pack-ground-truth", default="../cybergym_full_tasks/task_pack_ground_truth.json")
    parser.add_argument("--pocs-root", default="../cybergym_full_tasks/pocs")
    parser.add_argument("--jsonl-out", default="")
    parser.add_argument("--summary-out", default="")
    parser.add_argument("--limit", type=int, default=0, help="Limit tasks for smoke runs; 0 means all")
    args = parser.parse_args()

    rows, summary = evaluate_pack_ground_truth(
        tasks_json=args.tasks_json,
        format_ground_truth_json=args.format_ground_truth,
        pack_ground_truth_json=args.pack_ground_truth,
        pocs_root=args.pocs_root,
        limit=args.limit or None,
    )

    if args.jsonl_out:
        write_jsonl(args.jsonl_out, rows)
    if args.summary_out:
        write_summary(args.summary_out, summary)

    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
