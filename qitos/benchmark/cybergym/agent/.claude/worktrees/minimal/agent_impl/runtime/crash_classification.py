"""Classify one bounded candidate execution result."""

from __future__ import annotations

import re
from typing import Any


_SANITIZER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("asan", re.compile(r"AddressSanitizer|heap-buffer-overflow|stack-buffer-overflow|use-after-free|double-free", re.I)),
    ("msan", re.compile(r"MemorySanitizer|use-of-uninitialized-value", re.I)),
    ("ubsan", re.compile(r"UndefinedBehaviorSanitizer|runtime error:", re.I)),
    ("lsan", re.compile(r"LeakSanitizer|detected memory leaks", re.I)),
)

_SIGNAL_BY_CODE = {
    -4: "SIGILL",
    -6: "SIGABRT",
    -7: "SIGBUS",
    -8: "SIGFPE",
    -11: "SIGSEGV",
    -13: "SIGPIPE",
    -15: "SIGTERM",
    132: "SIGILL",
    134: "SIGABRT",
    135: "SIGBUS",
    136: "SIGFPE",
    139: "SIGSEGV",
    143: "SIGTERM",
}

_USAGE_RE = re.compile(
    r"\b(usage:|invalid (?:option|argument|input)|unknown option|expects? .{0,40}(?:file|argument)|"
    r"no such file|cannot open|failed to open|parse error|invalid magic|bad magic|unsupported format|"
    r"too short|truncated input|empty input)\b",
    re.I,
)

_FRAME_RE = re.compile(r"#\d+\s+(?:0x[0-9a-f]+\s+)?(?:in\s+)?([A-Za-z_~][\w:~.<>,]*)")


def classify_execution(
    *,
    returncode: int | None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    environment_error: str = "",
) -> dict[str, Any]:
    """Classify a bounded process run without treating all nonzero exits as crashes."""

    combined = f"{stdout or ''}\n{stderr or ''}"
    if environment_error:
        return _result("environment_error", environment_error=environment_error)
    if timed_out:
        return _result("timeout")

    for kind, pattern in _SANITIZER_PATTERNS:
        if pattern.search(combined):
            return _result(
                "sanitizer_failure",
                sanitizer_kind=kind,
                signal_name=_SIGNAL_BY_CODE.get(returncode or 0),
                top_frame=_top_frame(combined),
            )

    signal_name = _SIGNAL_BY_CODE.get(returncode or 0)
    if signal_name:
        return _result("signal_failure", signal_name=signal_name, top_frame=_top_frame(combined))

    if returncode not in (0, None) and _USAGE_RE.search(combined):
        return _result("input_rejected")

    if returncode == 0:
        return _result("clean_exit")

    # Nonzero but no crash signature.  Keep it out of crash accounting.
    return _result("input_rejected")


def _result(
    outcome: str,
    *,
    sanitizer_kind: str | None = None,
    signal_name: str | None = None,
    top_frame: str | None = None,
    environment_error: str = "",
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "sanitizer_kind": sanitizer_kind,
        "signal_name": signal_name,
        "top_frame": top_frame,
        "environment_error": environment_error,
    }


def _top_frame(text: str) -> str | None:
    for match in _FRAME_RE.finditer(text or ""):
        frame = match.group(1)
        if frame and "sanitizer" not in frame.lower():
            return frame[:160]
    return None
