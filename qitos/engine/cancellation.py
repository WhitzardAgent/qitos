"""Cancellation support for Engine runs.

Provides ``EngineResult.cancel(mode)`` to stop a running Engine
either immediately or after the current step completes.

Modes
-----
- ``"immediate"`` — signal the Engine loop to stop right away.
  The current step may be mid-execution; partial results are preserved.
- ``"after_step"`` — wait for the current step to finish before stopping.
  This ensures the step's reduce/critic/check_stop lifecycle completes.
"""

from __future__ import annotations

import threading
from enum import Enum


class CancelMode(str, Enum):
    """Cancellation mode for Engine runs."""

    NONE = "none"
    IMMEDIATE = "immediate"
    AFTER_STEP = "after_step"


class CancelToken:
    """Thread-safe cancellation signal shared between EngineResult and Engine.

    The Engine checks ``token.is_cancel_requested`` at each loop iteration
    and after each step. Setting the mode to ``"immediate"`` causes the
    next check to break; ``"after_step"`` waits until the step finishes.
    """

    def __init__(self) -> None:
        self._mode = CancelMode.NONE
        self._lock = threading.Lock()
        self._step_complete = threading.Event()

    @property
    def mode(self) -> CancelMode:
        with self._lock:
            return self._mode

    @property
    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self._mode != CancelMode.NONE

    def request_cancel(self, mode: str = "immediate") -> None:
        """Signal the Engine to cancel.

        Parameters
        ----------
        mode : str
            ``"immediate"`` or ``"after_step"``.
        """
        with self._lock:
            self._mode = CancelMode(mode)

    def clear(self) -> None:
        """Reset the token (called at the start of each Engine run)."""
        with self._lock:
            self._mode = CancelMode.NONE
        self._step_complete.clear()

    def mark_step_complete(self) -> None:
        """Signal that the current step has finished."""
        self._step_complete.set()

    def wait_for_step_complete(self, timeout: float = 30.0) -> bool:
        """Wait until the current step completes or timeout expires."""
        return self._step_complete.wait(timeout=timeout)

    def reset_step_event(self) -> None:
        """Reset the step-complete event for the next step."""
        self._step_complete.clear()


__all__ = ["CancelMode", "CancelToken"]
