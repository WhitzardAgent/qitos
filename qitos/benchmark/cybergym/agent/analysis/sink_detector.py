"""Full-file sink analysis behind a crash-isolated process boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def analyze_sink_file_isolated(
    repository: Path,
    relative_path: str,
    *,
    sink_function: str,
    line: int,
    description: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    path = (repository / relative_path).resolve()
    try:
        path.relative_to(repository.resolve())
    except ValueError:
        return {"status": "error", "reason": "outside_repository", "candidates": []}
    if not path.is_file():
        return {"status": "not_found", "reason": "target_file_missing", "candidates": []}

    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (package_parent, env.get("PYTHONPATH", "")) if item
    )
    payload = {
        "path": str(path),
        "relative_path": relative_path,
        "sink_function": sink_function,
        "line": line,
        "description": description,
        "budget_ms": max(100, int(timeout * 700)),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", f"{__package__}._sink_worker"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=max(.5, timeout),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "partial", "reason": "sink_analysis_timeout", "candidates": []}
    except Exception as exc:
        return {"status": "partial", "reason": "sink_worker_error", "detail": str(exc)[:200], "candidates": []}
    if result.returncode != 0:
        reason = "sink_worker_crash" if result.returncode in {-11, 139, -6, 134} else "sink_worker_error"
        return {"status": "partial", "reason": reason, "exit_code": result.returncode, "candidates": []}
    try:
        value = json.loads(result.stdout)
    except Exception as exc:
        return {"status": "partial", "reason": "sink_worker_bad_output", "detail": str(exc)[:200], "candidates": []}
    value["status"] = "success" if value.get("sink_resolved") else "partial"
    return value
