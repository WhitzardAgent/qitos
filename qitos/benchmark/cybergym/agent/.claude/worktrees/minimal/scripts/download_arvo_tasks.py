"""Download specific arvo tasks from HuggingFace for evaluation.

Downloads only the files needed for Level-1 evaluation:
description.txt + repo-vul.tar.gz for each specified task.

Usage:
    python scripts/download_arvo_tasks.py --task-ids 368 509 759 --output-dir /path/to/workspace
    python scripts/download_arvo_tasks.py --csv ground_truth.csv --output-dir /path/to/workspace
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import List, Optional


HF_REPO = "sunblaze-ucb/cybergym"


def download_task(task_id: str, output_dir: Path, extract: bool = True) -> Path:
    """Download a single arvo task from HuggingFace.

    Args:
        task_id: Task ID like "arvo:368" or just "368".
        output_dir: Base output directory.
        extract: Whether to extract repo-vul.tar.gz.

    Returns:
        Path to the task directory.
    """
    from huggingface_hub import hf_hub_download

    # Parse issue_id from task_id
    if ":" in str(task_id):
        issue_id = str(task_id).split(":")[-1]
    else:
        issue_id = str(task_id)

    task_dir = output_dir / f"arvo_{issue_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    hf_prefix = f"data/arvo/{issue_id}"

    # Download description.txt
    try:
        desc_path = hf_hub_download(
            HF_REPO,
            f"{hf_prefix}/description.txt",
            local_dir=str(output_dir),
        )
        # Move to task dir if needed
        src = output_dir / hf_prefix / "description.txt"
        dst = task_dir / "description.txt"
        if src.exists() and not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception as e:
        print(f"  Warning: could not download description.txt for {task_id}: {e}")

    # Download repo-vul.tar.gz
    try:
        archive_path = hf_hub_download(
            HF_REPO,
            f"{hf_prefix}/repo-vul.tar.gz",
            local_dir=str(output_dir),
        )
        src = output_dir / hf_prefix / "repo-vul.tar.gz"
        dst = task_dir / "repo-vul.tar.gz"
        if src.exists() and not dst.exists():
            import shutil
            shutil.copy2(str(src), str(dst))

        # Extract if requested
        if extract and dst.exists():
            repo_vul_dir = task_dir / "repo-vul"
            if not repo_vul_dir.exists():
                try:
                    with tarfile.open(str(dst), "r:gz") as tar:
                        tar.extractall(path=str(task_dir))
                except Exception as e:
                    print(f"  Warning: extraction failed for {task_id}: {e}")
    except Exception as e:
        print(f"  Warning: could not download repo-vul.tar.gz for {task_id}: {e}")

    # Generate submit.sh if not present
    submit_sh = task_dir / "submit.sh"
    if not submit_sh.exists():
        submit_sh.write_text(
            f'#!/bin/bash\ncurl -X POST http://localhost:8000/submit-vul '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"task_id": "arvo:{issue_id}"}}\'\n',
            encoding="utf-8",
        )
        submit_sh.chmod(0o755)

    # Generate README.md if not present
    readme = task_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# CyberGym Task\n\nGenerate a PoC for the vulnerability described in description.txt.\n",
            encoding="utf-8",
        )

    return task_dir


def main():
    parser = argparse.ArgumentParser(
        description="Download arvo tasks from HuggingFace for evaluation",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--task-ids", nargs="+", type=str,
        help="Task IDs to download (e.g., 368 509 or arvo:368 arvo:509)",
    )
    group.add_argument(
        "--csv", type=str,
        help="CSV file with task_id column",
    )
    parser.add_argument(
        "--output-dir", type=str, required=True,
        help="Output directory for task directories",
    )
    parser.add_argument(
        "--no-extract", action="store_true",
        help="Don't extract repo-vul.tar.gz",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect task IDs
    task_ids: List[str] = []
    if args.task_ids:
        task_ids = args.task_ids
    elif args.csv:
        with open(args.csv, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tid = row.get("task_id", "").strip()
                if tid:
                    task_ids.append(tid)

    print(f"Downloading {len(task_ids)} tasks to {output_dir}")
    for i, tid in enumerate(task_ids, 1):
        print(f"[{i}/{len(task_ids)}] {tid}...")
        try:
            download_task(tid, output_dir, extract=not args.no_extract)
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"Done. Task directories in {output_dir}")


if __name__ == "__main__":
    main()
