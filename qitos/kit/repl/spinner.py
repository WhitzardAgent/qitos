"""Spinner animation for the REPL.

Shows a rotating spinner character while the model is thinking.
"""

from __future__ import annotations

import sys
import threading
from typing import Optional


# Spinner frames (Braille pattern dots)
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ANSI codes
DIM = "\033[2m"
RESET = "\033[0m"


class Spinner:
    """Background thread spinner that animates while the model is thinking."""

    def __init__(self, message: str = "Thinking"):
        self._message = message
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._active = False

    def start(self) -> None:
        """Start the spinner animation."""
        if self._active:
            return
        self._stop_event.clear()
        self._active = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the spinner and clear the line."""
        if not self._active:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._active = False
        # Clear the spinner line
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()

    def _spin(self) -> None:
        """Spinner animation loop."""
        idx = 0
        while not self._stop_event.is_set():
            frame = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
            sys.stdout.write(f"\r{DIM}{frame} {self._message}...{RESET}")
            sys.stdout.flush()
            idx += 1
            self._stop_event.wait(0.08)

    @property
    def active(self) -> bool:
        return self._active
