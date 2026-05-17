"""Checkpoint data and manager for Engine run persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CheckpointData:
    """Snapshot of Engine state at a given step."""

    run_id: str
    step_id: int
    state_dict: Dict[str, Any]
    step_records: List[Dict[str, Any]]
    runtime_events: List[Dict[str, Any]]
    budget: Dict[str, Any]
    token_usage: int
    task_text: str
    task_dict: Optional[Dict[str, Any]] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_id": self.step_id,
            "state_dict": self.state_dict,
            "step_records": self.step_records,
            "runtime_events": self.runtime_events,
            "budget": self.budget,
            "token_usage": self.token_usage,
            "task_text": self.task_text,
            "task_dict": self.task_dict,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> CheckpointData:
        return cls(
            run_id=payload["run_id"],
            step_id=payload["step_id"],
            state_dict=payload["state_dict"],
            step_records=payload["step_records"],
            runtime_events=payload["runtime_events"],
            budget=payload["budget"],
            token_usage=payload["token_usage"],
            task_text=payload["task_text"],
            task_dict=payload.get("task_dict"),
            timestamp=payload.get("timestamp", ""),
            schema_version=payload.get("schema_version", "v1"),
        )


class CheckpointManager:
    """Manages checkpoint save/load for Engine runs.

    Args:
        checkpoint_dir: Directory to store checkpoint files.
        interval: Save a checkpoint every N steps (default 1 = every step).
    """

    def __init__(self, checkpoint_dir: str, interval: int = 1):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.interval = max(1, interval)

    def should_checkpoint(self, step_id: int) -> bool:
        """Check if a checkpoint should be saved at this step."""
        return step_id > 0 and step_id % self.interval == 0

    def save(self, data: CheckpointData) -> Path:
        """Save a checkpoint to disk.

        Returns the path of the saved checkpoint file.
        """
        filename = f"checkpoint_{data.run_id}_{data.step_id:04d}.json"
        path = self.checkpoint_dir / filename
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        return path

    def load_latest(self, run_id: str) -> Optional[CheckpointData]:
        """Load the latest checkpoint for a given run_id."""
        checkpoints = self.list_checkpoints(run_id)
        if not checkpoints:
            return None
        return checkpoints[-1]

    def list_checkpoints(self, run_id: str) -> List[CheckpointData]:
        """List all checkpoints for a run_id, sorted by step_id."""
        pattern = f"checkpoint_{run_id}_*.json"
        results: List[CheckpointData] = []
        for path in self.checkpoint_dir.glob(pattern):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                results.append(CheckpointData.from_dict(payload))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        results.sort(key=lambda c: c.step_id)
        return results

    def cleanup(self, run_id: str, keep: int = 1) -> None:
        """Keep only the N most recent checkpoints for a run_id."""
        checkpoints = self.list_checkpoints(run_id)
        if len(checkpoints) <= keep:
            return
        to_remove = checkpoints[:-keep]
        for cp in to_remove:
            filename = f"checkpoint_{cp.run_id}_{cp.step_id:04d}.json"
            path = self.checkpoint_dir / filename
            try:
                path.unlink()
            except OSError:
                pass


__all__ = ["CheckpointData", "CheckpointManager"]
