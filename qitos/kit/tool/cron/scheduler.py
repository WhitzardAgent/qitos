"""CronScheduler — real cron/scheduling implementation for QitOS.

Uses APScheduler for cron-like scheduling with support for:
- Standard 5-field cron expressions
- One-shot and recurring jobs
- Durable persistence to .qitos/scheduled_tasks.json
- Prompt-based job execution (enqueues prompts into agent's run loop)
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ....core.tool import BaseTool, ToolPermission, ToolSpec


@dataclass
class CronJob:
    """A scheduled job."""

    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = False
    created_at: str = ""
    last_fired: Optional[str] = None
    fire_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronJob":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CronScheduler:
    """Manages scheduled jobs using APScheduler.

    Jobs can be one-shot (recurring=False) or recurring (recurring=True).
    Durable jobs persist to .qitos/scheduled_tasks.json and survive restarts.
    When a job fires, it enqueues a prompt into the agent's run loop via
    a callback.
    """

    def __init__(
        self,
        workspace_root: str = ".",
        on_fire: Optional[Callable[[str], None]] = None,
    ):
        self.workspace_root = os.path.abspath(workspace_root)
        self._on_fire = on_fire
        self._jobs: Dict[str, CronJob] = {}
        self._scheduler = None
        self._started = False

        # Load durable jobs
        self._durable_path = os.path.join(
            self.workspace_root, ".qitos", "scheduled_tasks.json"
        )
        self._load_durable_jobs()

    def _load_durable_jobs(self) -> None:
        """Load durable jobs from disk."""
        if not os.path.isfile(self._durable_path):
            return
        try:
            with open(self._durable_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for job_data in data.get("jobs", []):
                job = CronJob.from_dict(job_data)
                if job.durable:
                    self._jobs[job.id] = job
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _save_durable_jobs(self) -> None:
        """Save durable jobs to disk."""
        os.makedirs(os.path.dirname(self._durable_path), exist_ok=True)
        durable = [j.to_dict() for j in self._jobs.values() if j.durable]
        try:
            with open(self._durable_path, "w", encoding="utf-8") as f:
                json.dump({"jobs": durable}, f, indent=2)
        except OSError:
            pass

    def create_job(
        self,
        cron: str,
        prompt: str,
        recurring: bool = True,
        durable: bool = False,
    ) -> CronJob:
        """Create a new scheduled job.

        :param cron: Standard 5-field cron expression (M H DoM Mon DoW).
        :param prompt: Prompt to enqueue when the job fires.
        :param recurring: True = repeat on schedule, False = fire once.
        :param durable: True = persist to disk, survive restarts.
        """
        job_id = f"cron-{uuid.uuid4().hex[:8]}"
        job = CronJob(
            id=job_id,
            cron=cron,
            prompt=prompt,
            recurring=recurring,
            durable=durable,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._jobs[job_id] = job

        # Try to schedule with APScheduler
        self._schedule_job(job)

        if durable:
            self._save_durable_jobs()

        return job

    def delete_job(self, job_id: str) -> bool:
        """Delete a scheduled job.

        :returns: True if the job was found and deleted.
        """
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False

        # Remove from APScheduler
        self._unschedule_job(job_id)

        if job.durable:
            self._save_durable_jobs()

        return True

    def list_jobs(self) -> List[CronJob]:
        """List all scheduled jobs."""
        return list(self._jobs.values())

    def _schedule_job(self, job: CronJob) -> None:
        """Schedule a job with APScheduler (if available)."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            if self._scheduler is None:
                self._scheduler = BackgroundScheduler()
                self._scheduler.start()
                self._started = True

            # Parse cron expression
            parts = job.cron.split()
            if len(parts) != 5:
                return

            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )

            self._scheduler.add_job(
                self._fire_job,
                trigger=trigger,
                id=job.id,
                args=[job.id],
                replace_existing=True,
            )
        except ImportError:
            # APScheduler not available — jobs are tracked but not auto-fired
            pass

    def _unschedule_job(self, job_id: str) -> None:
        """Remove a job from APScheduler."""
        if self._scheduler is not None:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

    def _fire_job(self, job_id: str) -> None:
        """Called when a job fires."""
        job = self._jobs.get(job_id)
        if job is None:
            return

        job.fire_count += 1
        job.last_fired = datetime.now(timezone.utc).isoformat()

        # Notify callback
        if self._on_fire is not None:
            try:
                self._on_fire(job.prompt)
            except Exception:
                pass

        # One-shot jobs auto-delete after firing
        if not job.recurring:
            self._jobs.pop(job_id, None)
            self._unschedule_job(job_id)

        if job.durable:
            self._save_durable_jobs()

    def shutdown(self) -> None:
        """Shut down the scheduler."""
        if self._scheduler is not None and self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False


# ── Tool wrappers ──────────────────────────────────────────────────────────────


class CronCreateTool(BaseTool):
    """Tool to create a scheduled job."""

    def __init__(self, scheduler: CronScheduler):
        self._scheduler = scheduler
        spec = ToolSpec(
            name="CronCreate",
            description="Create a scheduled job that enqueues a prompt on a cron schedule.",
            permissions=ToolPermission(),
        )
        super().__init__(spec=spec)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        cron = args.get("cron", "")
        prompt = args.get("prompt", "")
        recurring = args.get("recurring", True)
        durable = args.get("durable", False)

        if not cron or not prompt:
            return {"status": "error", "error": "cron and prompt are required"}

        job = self._scheduler.create_job(
            cron=cron,
            prompt=prompt,
            recurring=recurring,
            durable=durable,
        )
        return {"status": "success", "created": True, "job": job.to_dict()}


class CronDeleteTool(BaseTool):
    """Tool to delete a scheduled job."""

    def __init__(self, scheduler: CronScheduler):
        self._scheduler = scheduler
        spec = ToolSpec(
            name="CronDelete",
            description="Delete a scheduled job by its ID.",
            permissions=ToolPermission(),
        )
        super().__init__(spec=spec)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        job_id = args.get("job_id", "")
        if not job_id:
            return {"status": "error", "error": "job_id is required"}
        deleted = self._scheduler.delete_job(job_id)
        return {
            "status": "success" if deleted else "not_found",
            "deleted": deleted,
            "job_id": job_id,
        }


class CronListTool(BaseTool):
    """Tool to list all scheduled jobs."""

    def __init__(self, scheduler: CronScheduler):
        self._scheduler = scheduler
        spec = ToolSpec(
            name="CronList",
            description="List all scheduled jobs.",
            permissions=ToolPermission(),
        )
        super().__init__(spec=spec)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        jobs = self._scheduler.list_jobs()
        return {
            "status": "success",
            "jobs": [j.to_dict() for j in jobs],
            "count": len(jobs),
        }
