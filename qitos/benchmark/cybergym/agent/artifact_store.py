"""Persistence for delegate-produced artifacts."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    producer: str
    payload: Dict[str, Any]
    parent_refs: List[str] = field(default_factory=list)
    path: str = ""
    created_at: str = ""
    schema_version: str = "v2"
    work_order_id: str = ""
    summary: str = ""


class ArtifactStore:
    MAX_CREATE_ATTEMPTS = 10

    def __init__(self, workspace_root):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.artifacts_dir = (
            self.workspace_root / ".agent" / "memory" / "project" / "artifacts"
        )

    def write_artifact(
        self,
        *,
        artifact_type: str,
        producer: str,
        payload: Dict[str, Any],
        parent_refs: Optional[List[str]] = None,
        work_order_id: Optional[str] = "",
        summary: Optional[str] = "",
    ) -> ArtifactRecord:
        artifacts_dir = self._ensure_under_workspace(self.artifacts_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        index_path = self._ensure_under_workspace(artifacts_dir / "INDEX.jsonl")
        record = None
        serialized = ""

        for _ in range(self.MAX_CREATE_ATTEMPTS):
            artifact_id = uuid4().hex
            relative_path = (
                Path(".agent")
                / "memory"
                / "project"
                / "artifacts"
                / f"{artifact_id}.json"
            )
            artifact_path = self._ensure_under_workspace(
                self.workspace_root / relative_path
            )
            record = ArtifactRecord(
                artifact_id=artifact_id,
                artifact_type=str(artifact_type),
                producer=str(producer),
                payload=copy.deepcopy(payload or {}),
                work_order_id="" if work_order_id is None else str(work_order_id),
                summary="" if summary is None else str(summary),
                parent_refs=list(parent_refs or []),
                path=relative_path.as_posix(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            data = asdict(record)
            serialized = json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"
            try:
                with artifact_path.open("x", encoding="utf-8") as artifact_file:
                    artifact_file.write(serialized)
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Unable to allocate unique artifact id")

        with index_path.open("a", encoding="utf-8") as index_file:
            index_file.write(serialized)

        return record

    def _ensure_under_workspace(self, target: Path) -> Path:
        resolved = target.expanduser().resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Artifact path escapes workspace: {target}") from exc
        return resolved
