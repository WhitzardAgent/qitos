"""Tool result summarization hook — compresses large tool results via LLM.

Implements the original pentagi's two-tier size management:
- Tier 1 (16KB-32KB): LLM summarization preserving technical details
- Tier 2 (>32KB): Hard head+tail truncation

This prevents context overflow from large terminal output or browser content
while preserving critical information (IPs, ports, errors, CVEs).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.engine.hooks import EngineHook, HookContext
from ..prompts.summarizer_prompt import TOOL_RESULT_SUMMARIZER_PROMPT


class ToolResultSummarizationHook(EngineHook):
    """Summarizes large tool results via LLM before they enter the history.

    Thresholds (configurable):
    - summarize_threshold (default 16384 = 16KB): results above this get LLM summarization
    - hard_truncate_threshold (default 32768 = 32KB): results above this get head+tail truncation
    """

    def __init__(
        self,
        llm: Any = None,
        summarize_threshold: int = 16384,
        hard_truncate_threshold: int = 32768,
    ):
        self.llm = llm
        self.summarize_threshold = summarize_threshold
        self.hard_truncate_threshold = hard_truncate_threshold

    def on_after_act(self, ctx: HookContext, engine: Any) -> None:
        """After tool execution, summarize or truncate large results."""
        if not self.llm:
            return

        for result in ctx.action_results:
            if not isinstance(result, dict):
                continue
            output = result.get("output", "")
            if not isinstance(output, str) or len(output) <= self.summarize_threshold:
                continue

            if len(output) > self.hard_truncate_threshold:
                # Tier 2: Hard head+tail truncation
                head = self.hard_truncate_threshold // 2
                tail = self.hard_truncate_threshold - head
                result["output"] = (
                    output[:head]
                    + f"\n... [truncated, {len(output)} chars total] ...\n"
                    + output[-tail:]
                )
                continue

            # Tier 1: LLM summarization for 16KB-32KB range
            try:
                summary = self._summarize(output, result.get("metadata", {}))
                result["output"] = "[SUMMARIZED] " + summary
            except Exception:
                # Fallback to head+tail truncation
                head = self.summarize_threshold // 2
                tail = self.summarize_threshold - head
                result["output"] = (
                    output[:head]
                    + f"\n... [truncated, {len(output)} chars total] ...\n"
                    + output[-tail:]
                )

    def _summarize(self, text: str, metadata: Dict[str, Any]) -> str:
        """Call LLM to summarize a large tool result."""
        tool_name = metadata.get("tool_name", "unknown")
        prompt = TOOL_RESULT_SUMMARIZER_PROMPT.format(
            tool_name=tool_name,
            result_length=len(text),
            result_text=text[:12000],  # Limit input to summarizer
        )
        response = self.llm.chat([{"role": "user", "content": prompt}])
        if isinstance(response, str):
            return response
        if isinstance(response, dict) and "content" in response:
            return response["content"]
        return str(response)


__all__ = ["ToolResultSummarizationHook"]
