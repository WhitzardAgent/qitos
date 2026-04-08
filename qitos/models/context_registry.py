"""Conservative context-window inference for common model identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class _ContextPattern:
    prefixes: tuple[str, ...]
    window: int


_PATTERNS: tuple[_ContextPattern, ...] = (
    _ContextPattern(
        prefixes=(
            "qwen/",
            "qwen3",
            "qwen2.5",
            "qwen2",
            "qwen-max",
            "qwen-plus",
            "qwen-turbo",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "kimi-k2.5",
            "kimi-k2",
            "kimi",
            "moonshot-v1",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "minimax-m2.5",
            "minimax-m1",
            "abab",
            "m2.5",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-oss-120b",
            "gpt-oss-20b",
            "gpt-oss",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "gemma-4-31b",
            "gemma-4-26b",
            "gemma-4",
            "gemma4",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "claude-opus-4.1",
            "claude-opus-4",
            "claude-sonnet-4",
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus",
            "claude-3-sonnet",
            "claude-3-haiku",
        ),
        window=200_000,
    ),
    _ContextPattern(
        prefixes=(
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ),
        window=1_048_576,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
        ),
        window=1_047_576,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-4o",
            "gpt-4o-mini",
            "chatgpt-4o-latest",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "o3",
            "o3-mini",
            "o4-mini",
            "codex-mini-latest",
        ),
        window=200_000,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-4-turbo",
            "gpt-4-0125-preview",
            "gpt-4-1106-preview",
        ),
        window=128_000,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-4-32k",
            "gpt-4-32k-0314",
            "gpt-4-32k-0613",
        ),
        window=32_768,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-4",
            "gpt-4-0613",
            "gpt-4-0314",
        ),
        window=8_192,
    ),
    _ContextPattern(
        prefixes=(
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0125",
            "gpt-3.5-turbo-1106",
            "gpt-3.5-turbo-16k",
        ),
        window=16_385,
    ),
)


def _normalize(model_name: Optional[str]) -> str:
    return str(model_name or "").strip().lower()


def infer_context_window(
    model_name: Optional[str], *, fallback: Optional[int] = None
) -> Optional[int]:
    """
    Infer a conservative context window from a model identifier.

    The mapping intentionally prefers exact, well-known aliases. Unknown models
    return `fallback` so callers can preserve a stable default.
    """

    normalized = _normalize(model_name)
    if not normalized:
        return fallback
    for pattern in _PATTERNS:
        if any(normalized.startswith(prefix) for prefix in pattern.prefixes):
            return int(pattern.window)
    return fallback


def known_context_patterns() -> Iterable[tuple[tuple[str, ...], int]]:
    """Return the built-in ordered prefix table for debugging and tests."""

    for pattern in _PATTERNS:
        yield pattern.prefixes, pattern.window
