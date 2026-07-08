"""Runtime evidence artifact helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_digest(path: Path) -> str:
    h = hashlib.blake2s(digest_size=12)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_runtime_artifact(
    *,
    workspace_root: str,
    candidate_digest: str,
    payload: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
) -> str:
    root = Path(workspace_root or ".").resolve()
    artifact_dir = root / ".agent" / "runtime_evidence" / candidate_digest
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if stdout:
        (artifact_dir / "stdout.txt").write_text(stdout, encoding="utf-8", errors="replace")
    if stderr:
        (artifact_dir / "stderr.txt").write_text(stderr, encoding="utf-8", errors="replace")
    try:
        return str(artifact_dir.relative_to(root))
    except ValueError:
        return str(artifact_dir)


def tail_text(value: str, limit: int = 4000) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[-limit:], True
