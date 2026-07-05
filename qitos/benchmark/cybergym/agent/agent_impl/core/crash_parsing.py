"""Crash parsing mixin — pure @staticmethod helpers for sanitizer output."""

from __future__ import annotations

import re


class CrashParsingMixin:
    """Static methods for parsing ASAN/MSAN/UBSAN crash output.

    These are stateless helpers; they do not reference ``self``.
    """

    @staticmethod
    def _parse_crash_type(stderr: str) -> str:
        """Parse crash type from sanitizer output."""
        if not stderr:
            return ""
        patterns = [
            r"(heap-buffer-overflow)",
            r"(stack-buffer-overflow)",
            r"(heap-use-after-free)",
            r"(stack-use-after-scope)",
            r"(use-of-uninitialized-value)",
            r"(signed-integer-overflow)",
            r"(unsigned-integer-overflow)",
            r"(null-pointer-dereference)",
            r"(double-free)",
            r"(heap-double-free)",
            r"(out-of-bounds)",
            r"(SEGV)",
            r"(SIGSEGV)",
            r"(SIGABRT)",
            r"(SIGFPE)",
        ]
        for pattern in patterns:
            match = re.search(pattern, stderr, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _parse_crash_location(stderr: str) -> str:
        """Parse crash location from sanitizer output."""
        if not stderr:
            return ""
        match = re.search(r'(\S+\.\w+:\d+(?::\d+)?)', stderr)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _parse_asan_stack_summary(stderr: str, max_frames: int = 4) -> str:
        """Extract top function names from ASAN/MSAN/UBSAN stack trace."""
        if not stderr:
            return ""
        frames = []
        for match in re.finditer(r'#\d+\s+0x[\da-f]+\s+in\s+(\w+)', stderr):
            fname = match.group(1)
            if fname.startswith("_") or fname in ("__sanitizer", "_start", "__libc_start_main"):
                continue
            frames.append(fname)
            if len(frames) >= max_frames:
                break
        if not frames:
            return ""
        return " <- ".join(frames)
