"""ExecutionMonitor — monitors execution and detects stuck states."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class ExecutionMonitor:
    """Monitors execution progress and detects anomalies.

    Tracks:
    - Step timing and progress rate
    - Error frequency and patterns
    - Resource usage indicators
    - Stuck detection signals
    """

    def __init__(
        self,
        max_steps_per_subtask: int = 15,
        max_consecutive_failures: int = 3,
        max_runtime_seconds: Optional[float] = None,
    ):
        self.max_steps_per_subtask = max_steps_per_subtask
        self.max_consecutive_failures = max_consecutive_failures
        self.max_runtime_seconds = max_runtime_seconds

        self._start_time: Optional[float] = None
        self._step_count: int = 0
        self._failure_count: int = 0
        self._last_progress_step: int = 0
        self._findings_count: int = 0

    def start(self) -> None:
        """Start monitoring."""
        self._start_time = time.monotonic()
        self._step_count = 0
        self._failure_count = 0
        self._last_progress_step = 0
        self._findings_count = 0

    def record_step(self, success: bool = True, new_findings: int = 0) -> None:
        """Record a step execution."""
        self._step_count += 1
        if success:
            self._failure_count = 0
        else:
            self._failure_count += 1

        if new_findings > 0:
            self._findings_count += new_findings
            self._last_progress_step = self._step_count

    @property
    def is_stuck(self) -> bool:
        """Check if execution appears stuck."""
        if self._failure_count >= self.max_consecutive_failures:
            return True
        if self._step_count > self.max_steps_per_subtask:
            return True
        return False

    @property
    def is_timeout(self) -> bool:
        """Check if execution has exceeded the time limit."""
        if self._start_time is None or self.max_runtime_seconds is None:
            return False
        return time.monotonic() - self._start_time > self.max_runtime_seconds

    @property
    def progress_summary(self) -> Dict[str, Any]:
        """Return a summary of execution progress."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "step_count": self._step_count,
            "failure_count": self._failure_count,
            "findings_count": self._findings_count,
            "elapsed_seconds": round(elapsed, 1),
            "is_stuck": self.is_stuck,
            "is_timeout": self.is_timeout,
            "steps_since_last_progress": self._step_count - self._last_progress_step,
        }

    def should_refine(self) -> bool:
        """Determine if refinement (RefinerAgent) should be triggered."""
        return self.is_stuck or self.is_timeout
