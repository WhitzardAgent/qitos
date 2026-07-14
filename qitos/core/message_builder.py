"""MessageBuilder protocol for customizing LLM message construction.

Agents can provide a ``message_builder`` attribute implementing this protocol
to take full control over how messages are assembled before being sent to the
LLM.  When no custom builder is provided, the engine falls back to its
default message construction logic (unchanged behavior).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class MessageBuildRequest:
    """Everything the engine provides to a MessageBuilder."""

    step_id: int
    state: Any  # StateSchema instance
    observation: Any  # Observation instance
    prompt_bundle: Any  # PromptBuildResult instance
    prepared: str  # agent.prepare(state) return value
    history: List[Dict[str, Any]]  # retrieved history messages
    record: Any  # StepRecord


@dataclass
class MessageBuildResult:
    """What a MessageBuilder returns to the engine."""

    messages: List[Dict[str, Any]]
    # Optional entries to append to the engine history.
    # Each dict must have at least: {"role": str, "content": str, "step_id": int}
    # Optional keys: "metadata", "tool_calls", "tool_call_id", "name"
    history_entries: List[Dict[str, Any]] = field(default_factory=list)
    # Optional transient working-state payload for the current request.  The
    # engine wraps generic state in <RUNTIME_CONTEXT>; an already delimited
    # <DECISION_CONTEXT> is delivered verbatim.  Neither form is persisted as
    # a synthetic history turn.
    runtime_context: Optional[str] = None
    # ``merge_tool`` appends the wrapped context to the final tool result;
    # ``user`` appends a trailing user message; ``none`` leaves messages intact.
    runtime_context_delivery: str = "none"


@runtime_checkable
class MessageBuilder(Protocol):
    """Protocol for agents that want full control over message construction."""

    def build_messages(self, request: MessageBuildRequest) -> MessageBuildResult:
        """Build the complete message list sent to the LLM.

        The returned ``messages`` are passed directly to the LLM without
        any further injection or wrapping by the engine.

        The returned ``history_entries`` are appended to the engine's
        history, replacing the engine's default history-append logic.

        ``runtime_context`` is transient request state.  Builders can request
        ``merge_tool`` delivery to keep native assistant/tool conversations
        closed on a tool result, with a safe user-message fallback when no
        merge target is available.
        """
        ...
