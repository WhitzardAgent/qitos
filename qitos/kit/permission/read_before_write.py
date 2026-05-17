"""Read-before-write enforcement for QitOS.

Ensures that files must be read before they can be written or edited.
Mirrors Claude Code's FileStateCache in readFileState.ts.

Tracks file content hash and mtime at read time, and rejects writes
to files that haven't been read or that have been modified since read.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class FileReadState:
    """Cached state of a file at read time."""

    content_hash: str  # SHA-256 of file content at read time
    mtime: float  # os.path.getmtime() at read time


class ReadBeforeWriteEnforcer:
    """Enforces that files must be read before they can be written/edited.

    Tracks file reads with content hash + mtime. Before a write:
    - If file not in cache: reject (must read first)
    - If mtime changed and content changed: reject (stale read)
    - If mtime changed but content same (cloud-sync false positive): allow
    - If in cache and unchanged: allow
    """

    def __init__(self):
        self._cache: Dict[str, FileReadState] = {}

    def record_read(self, path: str, content: str) -> None:
        """Record that a file has been read.

        Called after a successful file read. Stores content hash + mtime.

        :param path: File path (will be normalized to absolute).
        :param content: File content as string.
        """
        abs_path = os.path.abspath(path)
        self._cache[abs_path] = FileReadState(
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            mtime=self._safe_mtime(abs_path) or 0.0,
        )

    def check_write(self, path: str) -> Tuple[bool, str]:
        """Check if a write/edit is allowed for the given path.

        :param path: File path to check.
        :returns: Tuple of (allowed, reason). If allowed is False,
                  reason contains the error message.
        """
        abs_path = os.path.abspath(path)

        # New files (don't exist yet) are always allowed
        if not os.path.exists(abs_path):
            return True, ""

        if abs_path not in self._cache:
            return False, (
                "File has not been read yet. Read it first before writing to it."
            )

        cached = self._cache[abs_path]
        current_mtime = self._safe_mtime(abs_path)

        # If mtime hasn't changed, file is unchanged since read
        if current_mtime is not None and abs(current_mtime - cached.mtime) > 1e-6:
            # mtime changed - check content to avoid false positives
            # (cloud sync, Windows file system, etc.)
            try:
                current_content = self._read_file_content(abs_path)
                current_hash = hashlib.sha256(
                    current_content.encode("utf-8")
                ).hexdigest()
                if current_hash != cached.content_hash:
                    return False, (
                        "File has been modified since read. "
                        "Read it again before attempting to write it."
                    )
            except (OSError, UnicodeDecodeError):
                return False, (
                    "File has been modified since read. "
                    "Read it again before attempting to write it."
                )

        return True, ""

    def invalidate(self, path: str) -> None:
        """Remove a file from the cache after a write.

        After a successful write, the cached read state is stale
        (content has changed). Remove it so the next read re-records.
        """
        self._cache.pop(os.path.abspath(path), None)

    def is_read(self, path: str) -> bool:
        """Check if a file has been read (is in the cache)."""
        return os.path.abspath(path) in self._cache

    def clear(self) -> None:
        """Clear all cached read states."""
        self._cache.clear()

    @property
    def tracked_files(self) -> int:
        """Number of files currently tracked."""
        return len(self._cache)

    @staticmethod
    def _safe_mtime(path: str) -> Optional[float]:
        """Safely get mtime, returning None on error."""
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    @staticmethod
    def _read_file_content(path: str) -> str:
        """Read file content for hash comparison."""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
