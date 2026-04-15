"""Window history implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.history import History, HistoryMessage


class WindowHistory(History):
    def __init__(self, window_size: int = 24):
        self.window_size = int(window_size)
        self._messages: List[HistoryMessage] = []
        self._pending_runtime_events: List[Dict[str, Any]] = []
        self._last_message_metadata: List[Dict[str, Any]] = []

    def append(self, message: HistoryMessage) -> None:
        self._messages.append(message)
        self.evict()

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[HistoryMessage]:
        query = query or {}
        max_items = int(
            query.get(
                "max_items",
                self.window_size if self.window_size > 0 else len(self._messages),
            )
        )
        roles = query.get("roles")
        step_min = query.get("step_min")
        step_max = query.get("step_max")

        items = list(self._messages)
        if roles:
            role_set = set(roles)
            items = [m for m in items if m.role in role_set]
        if step_min is not None:
            items = [m for m in items if m.step_id >= int(step_min)]
        if step_max is not None:
            items = [m for m in items if m.step_id <= int(step_max)]
        if max_items > 0:
            items = items[-max_items:]
        pending = str(query.get("pending_content") or "")
        budget = int(query.get("max_tokens") or 0)
        before_tokens = self._estimate_tokens(items) + self._estimate_text_tokens(
            pending
        )
        stage = "within_budget"
        if budget > 0 and before_tokens > budget:
            stage = "compact_skipped"
        self._pending_runtime_events = [
            {
                "stage": "context_history",
                "context": {
                    "stage": stage,
                    "before_tokens": before_tokens,
                    "after_tokens": before_tokens,
                    "saved_tokens": 0,
                    "budget": budget or None,
                    "pending_tokens": self._estimate_text_tokens(pending),
                    "messages_before": len(items),
                    "messages_after": len(items),
                    "strategy": "window_history",
                    "warning_ratio": query.get("warning_ratio"),
                    "reason": (
                        None
                        if stage == "within_budget"
                        else "window_history_no_compactor"
                    ),
                },
            }
        ]
        self._last_message_metadata = [self._metadata_for_message(m) for m in items]
        return items

    def summarize(self, max_items: int = 5) -> str:
        items = self.retrieve(query={"max_items": max_items})
        lines = [f"[{m.step_id}] {m.role}: {str(m.content)[:120]}" for m in items]
        return "\n".join(lines)

    def evict(self) -> int:
        if self.window_size <= 0 or len(self._messages) <= self.window_size:
            return 0
        removed = len(self._messages) - self.window_size
        self._messages = self._messages[-self.window_size :]
        return removed

    def reset(self, run_id: Optional[str] = None) -> None:
        self._messages = []
        self._pending_runtime_events = []
        self._last_message_metadata = []

    @property
    def messages(self) -> List[HistoryMessage]:
        return list(self._messages)

    def consume_runtime_events(self) -> List[Dict[str, Any]]:
        events = list(self._pending_runtime_events)
        self._pending_runtime_events = []
        return events

    def get_last_message_metadata(self) -> List[Dict[str, Any]]:
        return list(self._last_message_metadata)

    def _metadata_for_message(self, message: HistoryMessage) -> Dict[str, Any]:
        meta = dict(message.metadata or {})
        meta.setdefault("role", message.role)
        meta.setdefault("step_id", message.step_id)
        meta.setdefault("content_chars", len(str(message.content or "")))
        if message.tool_call_id:
            meta.setdefault("tool_call_id", message.tool_call_id)
        if message.tool_calls:
            meta.setdefault("tool_calls_count", len(message.tool_calls))
        if message.name:
            meta.setdefault("name", message.name)
        return meta

    def _estimate_tokens(self, messages: List[HistoryMessage]) -> int:
        return sum(self._estimate_text_tokens(m.content) for m in messages)

    def _estimate_text_tokens(self, text: Any) -> int:
        s = str(text or "")
        if not s:
            return 0
        return max(1, len(s) // 4)


__all__ = ["WindowHistory"]
