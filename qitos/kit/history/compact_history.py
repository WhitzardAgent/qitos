"""Compact-aware history implementation for long-running agent loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from qitos.core.history import History, HistoryMessage


@dataclass
class CompactConfig:
    """Configuration for `CompactHistory`."""

    max_tokens: int = 16000
    keep_last_rounds: int = 2
    keep_last_messages: int = 8
    hard_window: int = 96
    warning_ratio: float = 0.8
    auto_compact: bool = True
    compact_long_messages_over_chars: int = 900
    microcompact_preview_chars: int = 220
    summary_max_chars: int = 1400
    summary_input_message_limit: int = 28
    summary_metadata_source: str = "compact_history"
    emit_skipped_events: bool = True


class MessageGrouper:
    """Group messages into compactable rounds."""

    def group(self, messages: Iterable[HistoryMessage]) -> List[List[HistoryMessage]]:
        items = list(messages)
        if not items:
            return []

        step_groups = self._group_by_step(items)
        if len(step_groups) > 1:
            return step_groups
        return self._group_by_assistant_boundary(items)

    def _group_by_step(self, items: List[HistoryMessage]) -> List[List[HistoryMessage]]:
        groups: List[List[HistoryMessage]] = []
        current: List[HistoryMessage] = []
        current_step: Optional[int] = None
        for msg in items:
            step = int(getattr(msg, "step_id", 0))
            if current and current_step is not None and step != current_step:
                groups.append(current)
                current = []
            current.append(msg)
            current_step = step
        if current:
            groups.append(current)
        return groups

    def _group_by_assistant_boundary(
        self, items: List[HistoryMessage]
    ) -> List[List[HistoryMessage]]:
        groups: List[List[HistoryMessage]] = []
        current: List[HistoryMessage] = []
        seen_assistant = False
        for msg in items:
            if current and msg.role == "assistant" and seen_assistant:
                groups.append(current)
                current = [msg]
                seen_assistant = True
                continue
            current.append(msg)
            if msg.role == "assistant":
                seen_assistant = True
        if current:
            groups.append(current)
        return groups


class MicroCompactor:
    """Apply low-cost compaction to older, high-token messages."""

    def __init__(self, config: CompactConfig):
        self.config = config

    def compact(self, messages: Iterable[HistoryMessage]) -> List[HistoryMessage]:
        return [self._compact_message(msg) for msg in messages]

    def _compact_message(self, message: HistoryMessage) -> HistoryMessage:
        text = str(message.content or "")
        if message.metadata.get("summary"):
            return message
        if len(text) <= int(self.config.compact_long_messages_over_chars):
            return message

        preview = max(60, int(self.config.microcompact_preview_chars))
        head = text[:preview].rstrip()
        tail = text[-min(preview // 2, len(text)) :].lstrip()
        newline_count = text.count("\n")
        blob_kind = self._infer_blob_kind(message, text)
        compacted = (
            f"[Compacted {blob_kind} from step {message.step_id}; "
            f"original_chars={len(text)}; original_lines={newline_count + 1}]\n"
            f"{head}"
        )
        if tail and tail != head:
            compacted += f"\n...\n{tail}"

        metadata = dict(message.metadata)
        metadata.update(
            {
                "compacted": True,
                "compaction_mode": "micro",
                "original_chars": len(text),
                "original_lines": newline_count + 1,
            }
        )
        return HistoryMessage(
            role=message.role,
            content=compacted,
            step_id=message.step_id,
            tool_calls=[dict(x) for x in list(message.tool_calls or [])],
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=metadata,
        )

    def _infer_blob_kind(self, message: HistoryMessage, text: str) -> str:
        source = str(message.metadata.get("source", "")).strip().lower()
        role = str(message.role).strip().lower() or "message"
        lowered = text.lower()
        if any(
            token in lowered
            for token in ("traceback", "stderr", "stdout", "returncode")
        ):
            return "tool output"
        if any(
            token in lowered
            for token in ("http", "<html", "```html", "response headers")
        ):
            return "web/file result"
        if source:
            return f"{source} {role} message"
        return f"{role} message"


class SummaryCompactor:
    """Summarize older rounds into one continuation message."""

    def __init__(self, config: CompactConfig, llm: Any | None = None):
        self.config = config
        self.llm = llm

    def summarize(self, messages: Iterable[HistoryMessage]) -> str:
        items = list(messages)
        if not items:
            return ""

        prompt = self._summary_prompt(items)
        if self.llm is not None:
            try:
                response = self.llm(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Summarize earlier agent interaction for continuation. "
                                "Preserve user intent, constraints, discoveries, failed attempts, "
                                "file/code references, tool findings, current status, and next step."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
                summary = str(response or "").strip()
                if summary:
                    return summary[: int(self.config.summary_max_chars)]
            except Exception:
                pass

        return self._heuristic_summary(items)

    def _summary_prompt(self, messages: List[HistoryMessage]) -> str:
        body_items = messages[-int(self.config.summary_input_message_limit) :]
        body = "\n".join(f"[{m.step_id}] {m.role}: {m.content}" for m in body_items)
        return (
            "Create a compact continuation summary of the earlier conversation.\n"
            "Keep these sections concise:\n"
            "1. Goal and constraints\n"
            "2. Important discoveries and tool results\n"
            "3. Failed attempts or caveats\n"
            "4. Current state and pending work\n"
            "5. Suggested next step\n\n"
            f"{body}"
        )

    def _heuristic_summary(self, messages: List[HistoryMessage]) -> str:
        user_goal = ""
        assistant_notes: List[str] = []
        for msg in messages:
            content = str(msg.content or "").strip()
            if not content:
                continue
            snippet = content[:160].replace("\n", " ")
            if msg.role == "user" and not user_goal:
                user_goal = snippet
            elif msg.role == "assistant":
                assistant_notes.append(snippet)

        lines = ["Continuation summary of earlier context:"]
        if user_goal:
            lines.append(f"- Goal: {user_goal}")
        if assistant_notes:
            lines.append(f"- Findings: {assistant_notes[-1]}")
            if len(assistant_notes) > 1:
                lines.append(f"- Prior attempt: {assistant_notes[-2]}")
        last = messages[-1]
        lines.append(
            f"- Pending: Continue from step {last.step_id} with the latest trajectory in mind."
        )
        return "\n".join(lines)[: int(self.config.summary_max_chars)]


class CompactionController:
    """Coordinate threshold checks, microcompact, and summary compact."""

    def __init__(
        self,
        config: CompactConfig,
        *,
        llm: Any | None = None,
        grouper: MessageGrouper | None = None,
        micro: MicroCompactor | None = None,
        summary: SummaryCompactor | None = None,
    ):
        self.config = config
        self.grouper = grouper or MessageGrouper()
        self.micro = micro or MicroCompactor(config)
        self.summary = summary or SummaryCompactor(config, llm=llm)

    def retrieve(
        self,
        items: List[HistoryMessage],
        *,
        budget: int,
        pending_content: str,
        auto_compact: bool,
    ) -> tuple[List[HistoryMessage], List[Dict[str, Any]], List[Dict[str, Any]]]:
        events: List[Dict[str, Any]] = []
        before_tokens = self._estimate_tokens(items) + self._estimate_text_tokens(
            pending_content
        )
        warning_threshold = max(1, int(budget * float(self.config.warning_ratio)))
        metadata = [self._metadata_for_message(m) for m in items]

        if before_tokens >= warning_threshold:
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "warning",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": self._estimate_text_tokens(pending_content),
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "warning_threshold": warning_threshold,
                    },
                }
            )

        if budget <= 0 or before_tokens <= budget:
            if before_tokens < warning_threshold or self.config.emit_skipped_events:
                events.append(
                    {
                        "stage": "context_history",
                        "context": {
                            "stage": "within_budget",
                            "before_tokens": before_tokens,
                            "after_tokens": before_tokens,
                            "saved_tokens": 0,
                            "budget": budget,
                            "pending_tokens": self._estimate_text_tokens(
                                pending_content
                            ),
                            "messages_before": len(items),
                            "messages_after": len(items),
                            "strategy": "compact_history",
                            "warning_ratio": float(self.config.warning_ratio),
                            "reason": "within_budget",
                        },
                    }
                )
            return items, events, metadata

        if not auto_compact:
            if self.config.emit_skipped_events:
                events.append(
                    {
                        "stage": "context_history",
                        "context": {
                            "stage": "compact_skipped",
                            "before_tokens": before_tokens,
                            "after_tokens": before_tokens,
                            "saved_tokens": 0,
                            "budget": budget,
                            "pending_tokens": self._estimate_text_tokens(
                                pending_content
                            ),
                            "messages_before": len(items),
                            "messages_after": len(items),
                            "strategy": "compact_history",
                            "warning_ratio": float(self.config.warning_ratio),
                            "reason": "auto_compact_disabled",
                        },
                    }
                )
            return items, events, metadata

        groups = self.grouper.group(items)
        keep_rounds = max(1, int(self.config.keep_last_rounds))
        preserved_groups = groups[-keep_rounds:]
        older_groups = groups[:-keep_rounds]
        preserved = [msg for group in preserved_groups for msg in group]
        older = [msg for group in older_groups for msg in group]

        if older:
            compacted_older = self.micro.compact(older)
            micro_candidate = [*compacted_older, *preserved]
            after_micro_tokens = self._estimate_tokens(
                micro_candidate
            ) + self._estimate_text_tokens(pending_content)
            if after_micro_tokens < before_tokens:
                events.append(
                    {
                        "stage": "context_history",
                        "context": {
                            "stage": "microcompact_applied",
                            "before_tokens": before_tokens,
                            "after_tokens": after_micro_tokens,
                            "saved_tokens": max(0, before_tokens - after_micro_tokens),
                            "budget": budget,
                            "pending_tokens": self._estimate_text_tokens(
                                pending_content
                            ),
                            "messages_before": len(items),
                            "messages_after": len(micro_candidate),
                            "strategy": "compact_history",
                            "warning_ratio": float(self.config.warning_ratio),
                            "messages_compacted": sum(
                                1
                                for msg in compacted_older
                                if msg.metadata.get("compaction_mode") == "micro"
                            ),
                        },
                    }
                )
            if after_micro_tokens <= budget:
                return (
                    micro_candidate,
                    events,
                    [self._metadata_for_message(m) for m in micro_candidate],
                )

            summary_input = (
                older if self._estimate_tokens(older) <= budget else compacted_older
            )
            summary_text = self.summary.summarize(summary_input)
            summary_message = HistoryMessage(
                role="system",
                content=summary_text,
                step_id=older[-1].step_id,
                metadata={
                    "summary": True,
                    "source": self.config.summary_metadata_source,
                    "summarized_message_count": len(older),
                    "summarized_through_step": older[-1].step_id,
                },
            )
            summary_candidate = [summary_message, *preserved]
            after_summary_tokens = self._estimate_tokens(
                summary_candidate
            ) + self._estimate_text_tokens(pending_content)
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "summary_compact_applied",
                        "before_tokens": before_tokens,
                        "after_tokens": after_summary_tokens,
                        "saved_tokens": max(0, before_tokens - after_summary_tokens),
                        "budget": budget,
                        "pending_tokens": self._estimate_text_tokens(pending_content),
                        "messages_before": len(items),
                        "messages_after": len(summary_candidate),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "summarized_message_count": len(older),
                        "preserved_round_count": len(preserved_groups),
                    },
                }
            )
            trimmed_candidate = self._trim_to_budget(
                summary_candidate, budget=budget, pending_content=pending_content
            )
            return (
                trimmed_candidate,
                events,
                [self._metadata_for_message(m) for m in trimmed_candidate],
            )

        compacted = self.micro.compact(items)
        after_tokens = self._estimate_tokens(compacted) + self._estimate_text_tokens(
            pending_content
        )
        if after_tokens < before_tokens:
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "microcompact_applied",
                        "before_tokens": before_tokens,
                        "after_tokens": after_tokens,
                        "saved_tokens": max(0, before_tokens - after_tokens),
                        "budget": budget,
                        "pending_tokens": self._estimate_text_tokens(pending_content),
                        "messages_before": len(items),
                        "messages_after": len(compacted),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "messages_compacted": sum(
                            1
                            for msg in compacted
                            if msg.metadata.get("compaction_mode") == "micro"
                        ),
                    },
                }
            )
            return (
                self._trim_to_budget(
                    compacted, budget=budget, pending_content=pending_content
                ),
                events,
                [self._metadata_for_message(m) for m in compacted],
            )

        if self.config.emit_skipped_events:
            events.append(
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "compact_skipped",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": self._estimate_text_tokens(pending_content),
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "reason": "insufficient_prefix_to_compact",
                    },
                }
            )
        return (
            self._trim_to_budget(items, budget=budget, pending_content=pending_content),
            events,
            metadata,
        )

    def _trim_to_budget(
        self, items: List[HistoryMessage], *, budget: int, pending_content: str
    ) -> List[HistoryMessage]:
        trimmed = list(items)
        summary_head: List[HistoryMessage] = []
        if trimmed and trimmed[0].metadata.get("summary"):
            summary_head = [trimmed[0]]
            trimmed = trimmed[1:]
        keep_tail = max(1, int(self.config.keep_last_messages))
        if len(trimmed) > keep_tail:
            trimmed = trimmed[-keep_tail:]
        candidate = [*summary_head, *trimmed]
        while (
            len(trimmed) > 1
            and self._estimate_tokens(candidate)
            + self._estimate_text_tokens(pending_content)
            > budget
        ):
            trimmed.pop(0)
            candidate = [*summary_head, *trimmed]
        return [*summary_head, *trimmed]

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

    def _estimate_tokens(self, messages: Iterable[HistoryMessage]) -> int:
        return sum(self._estimate_text_tokens(m.content) for m in messages)

    def _estimate_text_tokens(self, text: Any) -> int:
        s = str(text or "")
        if not s:
            return 0
        return max(1, len(s) // 4)


class CompactHistory(History):
    """History implementation with threshold, microcompact, and summary compact."""

    def __init__(
        self,
        *,
        llm: Any | None = None,
        config: CompactConfig | None = None,
        max_tokens: Optional[int] = None,
        keep_last_rounds: Optional[int] = None,
        keep_last_messages: Optional[int] = None,
        hard_window: Optional[int] = None,
        auto_compact: Optional[bool] = None,
    ):
        cfg = config or CompactConfig()
        if max_tokens is not None:
            cfg.max_tokens = int(max_tokens)
        if keep_last_rounds is not None:
            cfg.keep_last_rounds = int(keep_last_rounds)
        if keep_last_messages is not None:
            cfg.keep_last_messages = int(keep_last_messages)
        if hard_window is not None:
            cfg.hard_window = int(hard_window)
        if auto_compact is not None:
            cfg.auto_compact = bool(auto_compact)

        self.llm = llm
        self.config = cfg
        self._messages: List[HistoryMessage] = []
        self._controller = CompactionController(cfg, llm=llm)
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
            query.get("max_items", len(items) if items else self.config.hard_window)
        )
        if max_items > 0:
            items = items[-max_items:]

        budget = int(query.get("max_tokens") or self.config.max_tokens)
        pending = str(query.get("pending_content") or "")
        auto_compact = bool(query.get("auto_compact", self.config.auto_compact))
        result, events, metadata = self._controller.retrieve(
            items,
            budget=budget,
            pending_content=pending,
            auto_compact=auto_compact,
        )
        self._pending_runtime_events = list(events)
        self._last_message_metadata = list(metadata)
        return result

    def summarize(self, max_items: int = 5) -> str:
        items = self._messages[-max_items:]
        return self._controller.summary.summarize(items)

    def evict(self) -> int:
        hard_window = int(self.config.hard_window)
        if hard_window <= 0 or len(self._messages) <= hard_window:
            return 0
        removed = len(self._messages) - hard_window
        self._messages = self._messages[-hard_window:]
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

    @property
    def messages(self) -> List[HistoryMessage]:
        return list(self._messages)

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


def compact_history(**kwargs: Any) -> CompactHistory:
    """Convenience builder for the compact history preset."""

    return CompactHistory(**kwargs)


__all__ = [
    "CompactConfig",
    "CompactHistory",
    "CompactionController",
    "MessageGrouper",
    "MicroCompactor",
    "SummaryCompactor",
    "compact_history",
]
