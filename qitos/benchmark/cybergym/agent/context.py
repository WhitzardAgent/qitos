"""Four-level progressive compaction pipeline for CyberGym context management.

Levels:
  1. Snip      -- Replace old tool results with clearance markers (no LLM call)
  2. MicroCompact -- Compress long messages to preview + tail (no LLM call)
  3. Collapse  -- Proactive restructuring at 90% context utilization
  4. AutoCompact -- LLM-based summarization of older rounds

Plus: CompactionCircuitBreaker and PostCompactRestorer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.history import HistoryMessage
from qitos.kit.history.compact_history import (
    CompactConfig,
    CompactHistory,
)


# ---------------------------------------------------------------------------
# Level 1: Snip
# ---------------------------------------------------------------------------

class SnipCompactor:
    """Replace old tool results with clearance markers (Level 1 compaction).

    Preserves message chain continuity (important for tool call ID references)
    while freeing context budget by replacing the content of old tool/observation
    messages with a short marker.
    """

    COMPRESSIBLE_ROLES = {"tool", "observation"}
    MARKER = "[Old tool result content cleared]"

    def __init__(self, keep_recent: int = 4):
        self.keep_recent = keep_recent

    def snip(self, messages: List[HistoryMessage]) -> List[HistoryMessage]:
        """Snip old compressible messages, keeping the most recent N."""
        compressible = [
            (i, msg)
            for i, msg in enumerate(messages)
            if msg.role in self.COMPRESSIBLE_ROLES
            and not msg.metadata.get("summary")
            and not msg.metadata.get("snipped")
        ]

        # Skip the most recent N compressible messages
        to_snip = (
            compressible[: -self.keep_recent]
            if len(compressible) > self.keep_recent
            else []
        )

        result = list(messages)
        for i, msg in to_snip:
            result[i] = HistoryMessage(
                role=msg.role,
                content=self.MARKER,
                step_id=msg.step_id,
                metadata={
                    **msg.metadata,
                    "snipped": True,
                    "original_chars": len(str(msg.content)),
                },
            )
        return result


# ---------------------------------------------------------------------------
# Level 3: Collapse
# ---------------------------------------------------------------------------

class CollapseGate:
    """Proactive compaction trigger at 90% context utilization.

    Prevents the agent from hitting the critical zone during a PoC
    verification step, which would be the worst time for compaction.
    """

    COLLAPSE_RATIO = 0.90

    def __init__(
        self,
        snip_compactor: SnipCompactor,
        compact_history: CompactHistory,
    ):
        self.snip = snip_compactor
        self.history = compact_history

    def should_collapse(self, current_tokens: int, budget_tokens: int) -> bool:
        if budget_tokens <= 0:
            return False
        return current_tokens >= int(budget_tokens * self.COLLAPSE_RATIO)

    def collapse(
        self, messages: List[HistoryMessage], budget: int
    ) -> List[HistoryMessage]:
        # Step 1: Snip old tool results
        snipped = self.snip.snip(messages)

        # Step 2: Force compact via CompactHistory controller
        result, _, _ = self.history._controller.retrieve(
            snipped,
            budget=budget,
            pending_content="",
            auto_compact=True,
        )
        return result


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_COMPACTION_FAILURES = 3


class CompactionCircuitBreaker:
    """Stop compaction after 3 consecutive failures.

    Prevents the compress-expand-recompress cycle that can occur when
    compaction repeatedly fails to free enough space.
    """

    def __init__(self):
        self._consecutive_failures: int = 0
        self._state: str = "closed"  # closed | half_open | open

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_COMPACTION_FAILURES:
            self._state = "open"

    def should_attempt(self) -> bool:
        return self._state != "open"


# ---------------------------------------------------------------------------
# Post-Compact File Restoration
# ---------------------------------------------------------------------------

POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 10_000


class PostCompactRestorer:
    """Restore critical files after compaction.

    After AutoCompact, restores the vulnerability description, current PoC draft,
    and last error trace as system-role messages so the agent doesn't lose
    critical context during the verification phase.
    """

    def restore(
        self,
        state: Any,
        compacted_messages: List[HistoryMessage],
    ) -> List[HistoryMessage]:
        """Restore critical context from state into compacted messages."""
        from .state import CyberGymState

        if not isinstance(state, CyberGymState):
            return compacted_messages

        restored: List[HistoryMessage] = []
        budget_remaining = POST_COMPACT_TOKEN_BUDGET

        # Always restore: vulnerability description
        if state.vulnerability_description:
            vuln_msg = HistoryMessage(
                role="system",
                content=(
                    f"[Restored context] Vulnerability description:\n"
                    f"{state.vulnerability_description}"
                ),
                step_id=0,
                metadata={
                    "source": "post_compact_restore",
                    "restored_file": "description",
                },
            )
            restored.append(vuln_msg)
            budget_remaining -= len(state.vulnerability_description) // 4

        # Restore current PoC draft if it exists
        if state.poc_path and budget_remaining > 0:
            poc_content = self._read_truncated(state.poc_path, state)
            if poc_content:
                restored.append(
                    HistoryMessage(
                        role="system",
                        content=(
                            f"[Restored context] Current PoC draft ({state.poc_path}):\n"
                            f"{poc_content}"
                        ),
                        step_id=0,
                        metadata={
                            "source": "post_compact_restore",
                            "restored_file": "poc_draft",
                        },
                    )
                )

        # Restore last error trace if it exists
        if state.last_error_trace and budget_remaining > 0:
            max_chars = POST_COMPACT_MAX_TOKENS_PER_FILE * 4
            trace_content = state.last_error_trace[:max_chars]
            restored.append(
                HistoryMessage(
                    role="system",
                    content=f"[Restored context] Last error trace:\n{trace_content}",
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "error_trace",
                    },
                )
            )

        # Restore harness info (submit.sh content) if available
        if state.harness_info and budget_remaining > 0:
            harness_content = state.harness_info[:2000]
            restored.append(
                HistoryMessage(
                    role="system",
                    content=f"[Restored context] Verification harness:\n{harness_content}",
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "harness_info",
                    },
                )
            )

        # Restore best PoC path if we have one (regression protection)
        if state.best_poc_path and budget_remaining > 0:
            restored.append(
                HistoryMessage(
                    role="system",
                    content=(
                        f"[Restored context] Best PoC so far: {state.best_poc_path} "
                        f"(score={state.best_poc_score})"
                    ),
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "best_poc",
                    },
                )
            )

        return [*compacted_messages, *restored]

    def _read_truncated(self, poc_path: str, state: Any) -> str:
        """Attempt to read the PoC file, truncated to token budget."""
        max_chars = POST_COMPACT_MAX_TOKENS_PER_FILE * 4
        try:
            from pathlib import Path

            p = Path(poc_path)
            if not p.is_absolute() and hasattr(state, "workspace_root"):
                p = Path(state.workspace_root) / poc_path
            if p.exists():
                content = p.read_text(encoding="utf-8", errors="replace")
                return content[:max_chars]
        except Exception:
            pass
        # Fallback: return a reference instead of content
        return f"[PoC file at {poc_path} -- could not read content for restoration]"


# ---------------------------------------------------------------------------
# CyberGym-specific compaction prompt
# ---------------------------------------------------------------------------

CYBERGYM_COMPACT_PROMPT = """Summarize the prior CyberGym PoC generation interaction.
Preserve these sections concisely:

1. **Vulnerability**: Bug type, affected component, trigger condition
2. **Investigation findings**: Vulnerable files, functions, data flow path
3. **Failed attempts**: What PoC strategies were tried and why they failed
4. **Current PoC state**: Current draft, last error trace, last verification result
5. **Next step**: What the agent should do next

Discard raw file contents, verbose shell output, and redundant search results.
Keep function signatures, error traces, and verification server responses."""


# ---------------------------------------------------------------------------
# CyberGymContextHistory -- the full pipeline
# ---------------------------------------------------------------------------

class CyberGymContextHistory(CompactHistory):
    """Context management with four-level progressive compaction for CyberGym.

    Extends QitOS CompactHistory with:
    - Level 1 Snip (pre-compaction pass replacing old tool results with markers)
    - Level 3 Collapse (proactive restructuring at 90% utilization)
    - Circuit breaker for compaction failures
    - Post-compact restoration of critical context
    """

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm, **kwargs)
        self.snip_compactor = SnipCompactor(keep_recent=4)
        self.collapse_gate = CollapseGate(self.snip_compactor, self)
        self.circuit_breaker = CompactionCircuitBreaker()
        self.post_compact_restorer = PostCompactRestorer()
        self._state_ref: Any = None  # set during agent init

    def set_state(self, state: Any) -> None:
        """Set a reference to the CyberGymState for post-compact restoration."""
        self._state_ref = state

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[HistoryMessage]:
        query = query or {}
        items = self._filter_messages(query)
        budget = int(query.get("max_tokens", self.config.max_tokens))

        # Level 1: Snip (always applies)
        items = self.snip_compactor.snip(items)

        # Estimate current tokens
        current_tokens = self._estimate_tokens_local(items)

        # Level 3: Collapse (proactive at 90%)
        if self.collapse_gate.should_collapse(current_tokens, budget):
            if self.circuit_breaker.should_attempt():
                try:
                    items = self.collapse_gate.collapse(items, budget)
                    self.circuit_breaker.record_success()
                except Exception:
                    self.circuit_breaker.record_failure()

        # Levels 2 + 4: CompactHistory (MicroCompact + Summary)
        # We apply snipped items to the internal messages temporarily
        self._messages = items
        result = super().retrieve(query, state, observation)

        # Post-compact restoration: if a summary was created, restore critical context
        if self._state_ref and any(
            m.metadata.get("source") == "compact_history" and m.metadata.get("summary")
            for m in result
        ):
            effective_state = state or self._state_ref
            result = self.post_compact_restorer.restore(effective_state, result)

        return result

    def _estimate_tokens_local(self, messages: List[HistoryMessage]) -> int:
        """Estimate token count for a list of messages."""
        total = 0
        for m in messages:
            text = str(m.content or "")
            total += max(1, len(text) // 4) if text else 0
        return total
