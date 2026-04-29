#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SuccessRateStats:
    total: int
    success: int
    stop_reasons: dict[str, int]

    @property
    def rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.success / self.total


def _manifest_paths(run_folder: Path) -> list[Path]:
    traces_dir = run_folder / "traces"
    if not traces_dir.is_dir():
        return []
    return sorted(traces_dir.glob("*/manifest.json"))


def _load_stop_reason(manifest_path: Path) -> str:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return "missing"
    stop_reason = summary.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason:
        return "missing"
    return stop_reason


def collect_success_rate(run_folder: Path | str) -> SuccessRateStats:
    root = Path(run_folder).expanduser()
    stop_reasons: Counter[str] = Counter()
    for manifest_path in _manifest_paths(root):
        stop_reasons[_load_stop_reason(manifest_path)] += 1
    total = sum(stop_reasons.values())
    return SuccessRateStats(
        total=total,
        success=stop_reasons.get("success", 0),
        stop_reasons=dict(stop_reasons),
    )


def _format_stats(stats: SuccessRateStats) -> str:
    lines = [
        f"success: {stats.success}/{stats.total} ({stats.rate * 100:.2f}%)",
        "stop_reason distribution:",
    ]
    for reason, count in sorted(stats.stop_reasons.items()):
        lines.append(f"  {reason}: {count}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count success stop_reason ratio under a CyberGym run folder."
    )
    parser.add_argument(
        "run_folder",
        help="CyberGym run folder, e.g. runs/cybergym/batch100_conc4_v1",
    )
    args = parser.parse_args(argv)

    stats = collect_success_rate(args.run_folder)
    print(_format_stats(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
