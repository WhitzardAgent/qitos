"""History implementation with token-budget-aware summarization."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.history import History, HistoryMessage


class TokenBudgetSummaryHistory(History):
    def __init__(
        self,
        *,
        llm: Any | None = None,
        max_tokens: int = 16000,
        keep_last: int = 8,
        hard_window: int = 64,
    ):
        self.llm = llm
        self.max_tokens = int(max_tokens)
        self.keep_last = int(keep_last)
        self.hard_window = int(hard_window)
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
        items = self._filter_messages(query)
        max_items = int(
            query.get("max_items", len(items) if items else self.hard_window)
        )
        if max_items > 0:
            items = items[-max_items:]

        budget = int(query.get("max_tokens") or self.max_tokens)
        pending = str(query.get("pending_content") or "")
        pending_tokens = self._estimate_text_tokens(pending)
        before_tokens = self._estimate_tokens(items) + pending_tokens
        events: List[Dict[str, Any]] = []
        metadata = [self._metadata_for_message(m) for m in items]
        if budget <= 0:
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "within_budget",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": None,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "token_budget_summary",
                        "warning_ratio": query.get("warning_ratio"),
                    },
                }
            )
            self._pending_runtime_events = events
            self._last_message_metadata = metadata
            return items
        if before_tokens <= budget:
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "within_budget",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "token_budget_summary",
                        "warning_ratio": query.get("warning_ratio"),
                    },
                }
            )
            self._pending_runtime_events = events
            self._last_message_metadata = metadata
            return items

        if len(items) <= self.keep_last:
            result = items[-self.keep_last :]
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "compact_skipped",
                        "before_tokens": before_tokens,
                        "after_tokens": self._estimate_tokens(result) + pending_tokens,
                        "saved_tokens": max(
                            0,
                            before_tokens
                            - (self._estimate_tokens(result) + pending_tokens),
                        ),
                        "budget": budget,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(items),
                        "messages_after": len(result),
                        "strategy": "token_budget_summary",
                        "warning_ratio": query.get("warning_ratio"),
                        "reason": "keep_last_floor",
                    },
                }
            )
            self._pending_runtime_events = events
            self._last_message_metadata = [
                self._metadata_for_message(m) for m in result
            ]
            return result

        recent = items[-self.keep_last :]
        old = items[: -self.keep_last]
        if not old:
            self._pending_runtime_events = events
            self._last_message_metadata = metadata
            return recent
        summary = self._summarize_messages(old)
        summary_message = HistoryMessage(
            role="system",
            content=summary,
            step_id=old[-1].step_id,
            metadata={"source": "token_budget_summary", "summary": True},
        )
        result = [summary_message, *recent]
        after_tokens = self._estimate_tokens(result) + pending_tokens
        events.extend(
            [
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "warning",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "token_budget_summary",
                        "warning_ratio": query.get("warning_ratio"),
                    },
                },
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "summary_compact_applied",
                        "before_tokens": before_tokens,
                        "after_tokens": after_tokens,
                        "saved_tokens": max(0, before_tokens - after_tokens),
                        "budget": budget,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(items),
                        "messages_after": len(result),
                        "strategy": "token_budget_summary",
                        "warning_ratio": query.get("warning_ratio"),
                        "summarized_message_count": len(old),
                    },
                },
            ]
        )
        self._pending_runtime_events = events
        self._last_message_metadata = [self._metadata_for_message(m) for m in result]
        return result

    def summarize(self, max_items: int = 5) -> str:
        items = self.retrieve(query={"max_items": max_items})
        return "\n".join(
            f"[{m.step_id}] {m.role}: {str(m.content)[:120]}" for m in items
        )

    def evict(self) -> int:
        if self.hard_window <= 0 or len(self._messages) <= self.hard_window:
            return 0
        removed = len(self._messages) - self.hard_window
        self._messages = self._messages[-self.hard_window :]
        return removed

    def reset(self, run_id: Optional[str] = None) -> None:
        self._messages = []
        self._pending_runtime_events = []
        self._last_message_metadata = []

    def consume_runtime_events(self) -> List[Dict[str, Any]]:
        events = list(self._pending_runtime_events)
        self._pending_runtime_events = []
        return events

    def get_last_message_metadata(self) -> List[Dict[str, Any]]:
        return list(self._last_message_metadata)

    def _filter_messages(self, query: Dict[str, Any]) -> List[HistoryMessage]:
        items = list(self._messages)
        roles = query.get("roles")
        step_min = query.get("step_min")
        step_max = query.get("step_max")
        if roles:
            role_set = {str(x) for x in roles}
            items = [m for m in items if m.role in role_set]
        if step_min is not None:
            items = [m for m in items if m.step_id >= int(step_min)]
        if step_max is not None:
            items = [m for m in items if m.step_id <= int(step_max)]
        return items

    def _summarize_messages(self, messages: List[HistoryMessage]) -> str:
        prompt = self._summary_prompt(messages)
        if self.llm is not None:
            try:
                response = self.llm(
                    [
                        {
                            "role": "system",
                            "content": "Summarize prior agent interaction for continuity. Preserve discoveries, failures, and pending work. Keep it concise and factual.",
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                summary = str(response or "").strip()
                if summary:
                    return summary
            except Exception:
                pass
        return self._heuristic_summary(messages)

    def _summary_prompt(self, messages: List[HistoryMessage]) -> str:
        body = "\n".join(f"[{m.step_id}] {m.role}: {m.content}" for m in messages)
        return (
            "Summarize the following interaction history for a continuation model call. "
            "Keep key findings, mistakes, tool results, and remaining intent.\n\n"
            f"{body}"
        )

    def _heuristic_summary(self, messages: List[HistoryMessage]) -> str:
        lines = [f"[{m.step_id}] {m.role}: {m.content[:160]}" for m in messages[-12:]]
        return "Summary of earlier interaction:\n" + "\n".join(lines)

    def _estimate_tokens(self, messages: List[HistoryMessage]) -> int:
        return sum(self._estimate_text_tokens(m.content) for m in messages)

    def _estimate_text_tokens(self, text: Any) -> int:
        s = str(text or "")
        if not s:
            return 0
        return max(1, len(s) // 4)

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


__all__ = ["TokenBudgetSummaryHistory"]
