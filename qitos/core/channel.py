"""Field-level reducer (channel) semantics for StateSchema.

Provides Annotated[type, reducer] pattern inspired by LangGraph channels.
Three built-in reducer semantics: Append, Replace, Ephemeral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ReducerFn = Callable[[Any, Any], Any]  # (current, update) -> new_value


@dataclass(frozen=True)
class Append:
    """Reducer that appends update to current (list/dict merge semantics).

    For list fields: extends the current list with update items.
    For dict fields: shallow-merges update into current dict.
    """

    def __call__(self, current: Any, update: Any) -> Any:
        if isinstance(current, list) and isinstance(update, list):
            result: Any = list(current)
            result.extend(update)
            return result
        if isinstance(current, dict) and isinstance(update, dict):
            result = dict(current)
            result.update(update)
            return result
        # Fallback: replace if types don't match append semantics
        return update


@dataclass(frozen=True)
class Replace:
    """Reducer that replaces current with update (default behavior)."""

    def __call__(self, current: Any, update: Any) -> Any:
        return update


@dataclass(frozen=True)
class Ephemeral:
    """Reducer where the value is only valid for the current step.

    After reduce, the field is reset to its default on the next step.
    The reducer itself acts like Replace during the current step.
    """

    reset_value: Any = None

    def __call__(self, current: Any, update: Any) -> Any:
        return update


# --- Built-in reducer functions (can be used directly in Annotated) ---


def last_value(current: Any, update: Any) -> Any:
    """Replace current with update (default, same as Replace)."""
    return update


def append_list(current: list, update: list) -> list:
    """Append update list items to current list."""
    result = list(current or [])
    result.extend(update or [])
    return result


def dict_merge(current: dict, update: dict) -> dict:
    """Shallow-merge update into current dict."""
    result = dict(current or {})
    result.update(update or {})
    return result


def add_messages(current: list, update: list) -> list:
    """LangGraph-style message reducer: append with ID-based dedup/replacement.

    If a message in update has the same `id` as one in current,
    the existing message is replaced. Otherwise, messages are appended.
    Messages can be dicts or objects with an `id` attribute.
    """
    result = list(current or [])
    existing_ids: dict[str, int] = {}
    for i, m in enumerate(result):
        mid = _msg_id(m)
        if mid is not None:
            existing_ids[mid] = i

    for msg in update or []:
        mid = _msg_id(msg)
        if mid is not None and mid in existing_ids:
            # Replace existing message with same ID
            result[existing_ids[mid]] = msg
        else:
            result.append(msg)
            if mid is not None:
                existing_ids[mid] = len(result) - 1
    return result


def _msg_id(msg: Any) -> str | None:
    """Extract ID from a message (dict or object with 'id' attr)."""
    if isinstance(msg, dict):
        return msg.get("id")
    return getattr(msg, "id", None)
