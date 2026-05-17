"""AutoStoreHook — automatically stores tool results in PentAGIMemory.

After each engine step, if the executed tools are in the allowed list,
their results are chunked and stored in the vector memory for later
RAG retrieval. This mirrors pentagi's `storeToolResult` which runs
after every tool execution in `allowedStoringInMemoryTools`.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Set

from qitos.engine.hooks import EngineHook, HookContext


# Tool names whose results should be auto-stored in memory.
# Mirrors pentagi's allowedStoringInMemoryTools.
ALLOWED_TOOLS: Set[str] = {
    "terminal", "read_file", "write_file", "list_files",
    "search", "search_duckduckgo", "search_searxng",
    "maintenance_result",
    "hack_result", "code_result", "enricher_result",
    "advice",
}

DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 100


def _chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks (simple character-based)."""
    if len(text) <= chunk_size:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - chunk_overlap
    return chunks


class AutoStoreHook(EngineHook):
    """Engine hook that auto-stores tool results in PentAGIMemory.

    After each step, inspects the action results for tools in the
    allowed list, chunks the output, and stores each chunk as a
    MemoryRecord with metadata (tool_name, step_id, flow_id, etc.).
    """

    def __init__(
        self,
        memory: Any,
        allowed_tools: Optional[Set[str]] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        flow_id: str = "",
    ):
        self._memory = memory
        self._allowed_tools = allowed_tools or ALLOWED_TOOLS
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._flow_id = flow_id

    def on_after_step(self, ctx: HookContext, engine: Any) -> None:
        record = ctx.record
        if record is None:
            return

        actions = getattr(record, "actions", []) or []
        action_results = getattr(record, "action_results", []) or []

        for idx, action in enumerate(actions):
            tool_name = getattr(action, "name", None) or (
                action.get("name") if isinstance(action, dict) else ""
            )
            if tool_name not in self._allowed_tools:
                continue

            result = action_results[idx] if idx < len(action_results) else None
            if result is None:
                continue

            # Format: combine tool name + args + result as markdown
            result_str = result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False, default=str
            )
            text = f"## Tool: {tool_name}\n{result_str}"

            chunks = _chunk_text(text, self._chunk_size, self._chunk_overlap)
            from qitos.core.memory import MemoryRecord

            for chunk_idx, chunk in enumerate(chunks):
                self._memory.append(MemoryRecord(
                    role="tool_result",
                    content=chunk,
                    step_id=ctx.step_id,
                    metadata={
                        "tool_name": tool_name,
                        "flow_id": self._flow_id,
                        "doc_type": "memory",
                        "part": chunk_idx,
                        "total_parts": len(chunks),
                    },
                ))


__all__ = ["AutoStoreHook", "ALLOWED_TOOLS"]
