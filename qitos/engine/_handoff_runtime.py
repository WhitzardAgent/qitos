"""Internal runtime for executing handoff decisions within the Engine loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .states import RuntimePhase

if TYPE_CHECKING:
    from .engine import Engine
    from ..core.decision import Decision

StateT = TypeVar("StateT")
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


# ---------------------------------------------------------------------------
# Context filters — control what history the target agent receives
# ---------------------------------------------------------------------------


class ContextFilter(ABC):
    """Abstract base for filtering conversation history during handoff."""

    @abstractmethod
    def filter_history(self, history: list, *, task: str = "") -> list:
        """Filter the conversation history for the target agent.

        Parameters
        ----------
        history : list
            Full conversation history from the source agent.
        task : str
            The original task description.

        Returns
        -------
        list
            Filtered history for the target agent.
        """


class FullContextFilter(ContextFilter):
    """Pass through the complete conversation history unchanged."""

    def filter_history(self, history: list, *, task: str = "") -> list:
        return list(history)


class SummaryContextFilter(ContextFilter):
    """Compress older history into a summary, keeping recent rounds intact.

    Parameters
    ----------
    keep_recent_rounds : int
        Number of recent rounds to keep intact (default 3).
    summary_prefix : str
        Prefix for the summary placeholder.
    """

    def __init__(
        self,
        keep_recent_rounds: int = 3,
        summary_prefix: str = "[Previous conversation summarized] ",
    ):
        self._keep_recent_rounds = keep_recent_rounds
        self._summary_prefix = summary_prefix

    def filter_history(self, history: list, *, task: str = "") -> list:
        max_items = self._keep_recent_rounds * 2
        if len(history) <= max_items:
            return list(history)

        return compact_handoff_history(
            history,
            max_items=max_items,
            summary_prefix=self._summary_prefix,
        )


class IsolatedContextFilter(ContextFilter):
    """Pass only the system prompt and task description.

    The target agent starts fresh with no conversation history.
    """

    def filter_history(self, history: list, *, task: str = "") -> list:
        # Keep only system messages
        system_msgs = []
        for item in history:
            if isinstance(item, dict):
                role = item.get("role", "")
            else:
                role = getattr(item, "role", "")
            if role == "system":
                system_msgs.append(item)

        # Add the task as a fresh user message
        if task:
            try:
                task_msg = type(history[0])(role="user", content=task) if history else {"role": "user", "content": task}
            except Exception:
                task_msg = {"role": "user", "content": task}
            system_msgs.append(task_msg)

        return system_msgs


# ---------------------------------------------------------------------------
# Context filter factory
# ---------------------------------------------------------------------------

def get_context_filter(strategy: str | Any) -> ContextFilter:
    """Get a ContextFilter instance for the given strategy.

    Parameters
    ----------
    strategy : str or ContextStrategy
        One of "full", "summary", "isolated".

    Returns
    -------
    ContextFilter
    """
    from ..core.agent_spec import ContextStrategy

    strategy_str = str(strategy).lower() if not isinstance(strategy, ContextStrategy) else strategy.value

    filters = {
        "full": FullContextFilter,
        "summary": SummaryContextFilter,
        "isolated": IsolatedContextFilter,
    }
    cls = filters.get(strategy_str)
    if cls is None:
        raise ValueError(f"Unknown context strategy: {strategy!r}. Expected one of {list(filters)}")
    return cls()


# ---------------------------------------------------------------------------
# Handoff result
# ---------------------------------------------------------------------------


@dataclass
class HandoffResult:
    """Result of a handoff execution."""

    from_agent: str
    to_agent: str
    context_strategy: str = ""
    messages_passed: int = 0


# ---------------------------------------------------------------------------
# Handoff runtime
# ---------------------------------------------------------------------------


class _HandoffRuntime(Generic[StateT, ObservationT, ActionT]):
    """Handles agent switching when a handoff decision is produced."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def execute_handoff(
        self,
        state: StateT,
        decision: Decision[ActionT],
        record: Any,
    ) -> HandoffResult:
        """Execute handoff: swap agent, rebuild dependencies, record trace."""

        from .action_executor import ActionExecutor
        from ..core.agent_spec import ContextStrategy

        target_name = decision.meta.get("handoff_target")
        if not target_name:
            raise ValueError("handoff decision requires meta['handoff_target']")

        if self.engine.agent_registry is None:
            raise ValueError("handoff requires agent_registry on Engine")

        spec = self.engine.agent_registry.resolve(target_name)

        # 1. State compatibility check / adaptation
        self._validate_state_compatibility(state, spec)

        # 2. Apply context filter based on strategy
        strategy = spec.context_strategy
        if isinstance(strategy, ContextStrategy):
            strategy_str = strategy.value
        else:
            strategy_str = str(strategy).lower()

        context_filter = get_context_filter(strategy_str)
        # Extract history items — handle both list-like and _EngineWindowHistory
        runtime_history = getattr(self.engine, "_runtime_history", None)
        if runtime_history is not None:
            if hasattr(runtime_history, "_items"):
                history = list(runtime_history._items)
            elif hasattr(runtime_history, "__iter__"):
                history = list(runtime_history)
            else:
                history = []
        else:
            history = []
        task = getattr(state, "task", "")
        filtered_history = context_filter.filter_history(history, task=task)
        messages_passed = len(filtered_history)

        # Apply filtered history
        if hasattr(self.engine, "_runtime_history") and self.engine._runtime_history is not None:
            if hasattr(self.engine._runtime_history, "_items"):
                self.engine._runtime_history._items = filtered_history
            elif hasattr(self.engine._runtime_history, "replace_all"):
                self.engine._runtime_history.replace_all(filtered_history)

        # 3. Apply HandoffContext if present (field filtering)
        self._apply_handoff_context(state, spec)

        # 4. Swap agent
        old_agent_name = self.engine.agent.name
        self.engine.agent = spec.agent
        self.engine.tool_registry = spec.agent.tool_registry

        # 4b. Set up SharedMemoryManager namespace for the new agent
        self._setup_shared_memory_namespace(old_agent_name, target_name)

        self.engine.executor = (
            ActionExecutor(
                tool_registry=self.engine.tool_registry,
                trace_writer=self.engine.trace_writer,
                delegate_depth=self.engine._delegate_depth,
                shared_memory=self.engine._shared_memory,
            )
            if self.engine.tool_registry is not None
            else None
        )

        # 5. Reset protocol and prompt cache so the new agent builds fresh ones
        self.engine._resolved_protocol = self.engine.resolve_protocol()
        self.engine._last_system_prompt = ""
        self.engine._last_prompt_metadata = {}

        # 6. Record agent_id on step
        record.agent_id = target_name

        # 7. Emit trace events with enriched metadata
        self.engine._emit(
            record.step_id,
            RuntimePhase.HANDOFF_START,
            payload={
                "from": old_agent_name,
                "to": target_name,
                "context_strategy": strategy_str,
                "messages_passed": messages_passed,
            },
        )
        self.engine._emit(
            record.step_id,
            RuntimePhase.HANDOFF_END,
            payload={
                "agent": target_name,
                "context_strategy": strategy_str,
            },
        )

        # 8. Write HandoffSpanData to TracingProvider if available
        self._write_handoff_span(
            old_agent_name, target_name, strategy_str, messages_passed
        )

        return HandoffResult(
            from_agent=old_agent_name,
            to_agent=target_name,
            context_strategy=strategy_str,
            messages_passed=messages_passed,
        )

    def _write_handoff_span(
        self,
        from_agent: str,
        to_agent: str,
        context_strategy: str,
        messages_passed: int,
    ) -> None:
        """Write a HandoffSpanData to the TracingProvider if configured."""
        provider = getattr(self.engine, "_tracing_provider", None)
        if provider is None:
            return

        try:
            from ..tracing.models import HandoffSpanData, SpanType

            span_data = HandoffSpanData(
                from_agent=from_agent,
                to_agent=to_agent,
                context_strategy=context_strategy,
                messages_passed=messages_passed,
            )
            with provider.create_trace(name=f"handoff:{from_agent}->{to_agent}") as trace:
                span = trace.create_span(SpanType.HANDOFF, span_data)
                span.start()
                span.finish()
        except Exception:
            # Tracing is best-effort; never block handoff on trace failures
            pass

    def _setup_shared_memory_namespace(
        self, old_agent_name: str, target_name: str
    ) -> None:
        """Ensure a SharedMemoryManager namespace exists for the target agent.

        If the engine has a SharedMemoryManager (stored as
        ``_shared_memory_manager``), creates writable namespaces for both
        the source and target agents.  If the spec declares
        ``shared_memory``, the target agent also gets access to the
        source agent's namespace as read-only.
        """
        from ..core.shared_memory import SharedMemoryManager

        mgr = getattr(self.engine, "_shared_memory_manager", None)
        if mgr is None:
            return

        if not isinstance(mgr, SharedMemoryManager):
            return

        # Ensure both agents have their own writable namespace
        mgr.namespace(old_agent_name)
        mgr.namespace(target_name)

        # Grant the target agent read access to the source agent's namespace
        # if the spec declares shared_memory
        registry = self.engine.agent_registry
        if registry is None:
            return
        spec = registry.resolve(target_name)
        if spec.shared_memory is not None:
            mgr.grant_read_access(target_name, old_agent_name)

    def _validate_state_compatibility(self, state: Any, spec: Any) -> None:
        """Check that state is compatible with the new agent.

        If a StateAdapter is provided on the spec, it will be used to adapt
        the state. Otherwise, we do a lightweight type check.
        """
        if spec.state_adapter is not None:
            adapted = spec.state_adapter.adapt(state)
            # Replace state in-place using setattr for proper descriptor handling
            # and to avoid leaking private attributes between state types
            if adapted is not state:
                for key, value in adapted.__dict__.items():
                    if not key.startswith('_'):
                        setattr(state, key, value)
        # Without an adapter, we trust that the new agent can handle the
        # existing state type. A type mismatch will surface as an error
        # when the new agent's reduce() or build_system_prompt() is called.

    def _apply_handoff_context(self, state: Any, spec: Any) -> None:
        """Apply HandoffContext settings: shared_state_fields, max_history_rounds, and payload."""
        from ..core.agent_spec import HandoffContext

        hc: HandoffContext | None = spec.handoff_context
        if hc is None:
            return

        # Truncate history if max_history_rounds is set (supplementary to context filter)
        if hc.max_history_rounds is not None:
            history = self.engine._runtime_history
            if hasattr(history, "truncate_to_rounds"):
                history.truncate_to_rounds(hc.max_history_rounds)
            elif hasattr(history, "_items"):
                max_items = hc.max_history_rounds * 2
                history._items = history._items[-max_items:]

        # Filter state fields if shared_state_fields is specified
        if hc.shared_state_fields:
            base_fields = {"schema_version", "task", "current_step", "max_steps",
                           "final_result", "stop_reason", "metadata", "metrics"}
            allowed = base_fields | set(hc.shared_state_fields)
            for key in list(state.__dict__.keys()):
                if key not in allowed:
                    state.metadata.setdefault("_handoff_removed_fields", {})[key] = state.__dict__.pop(key)

        # Write payload entries to target agent's shared memory namespace
        if hc.payload:
            from ..core.shared_memory import SharedMemoryManager
            mgr = getattr(self.engine, "_shared_memory_manager", None)
            if mgr is not None and isinstance(mgr, SharedMemoryManager):
                target_ns = mgr.namespace(spec.name)
                for key, value in hc.payload.items():
                    target_ns.write(f"handoff_payload:{key}", value)


def compact_handoff_history(
    history_items: list,
    max_items: int = 10,
    summary_prefix: str = "[Previous conversation summarized] ",
) -> list:
    """Compact a conversation history by summarizing older messages.

    Keeps the most recent `max_items` messages intact and replaces
    older messages with a single summary placeholder.

    This is used during handoff to prevent context explosion when
    transferring between agents.

    Args:
        history_items: List of history messages to compact.
        max_items: Number of recent messages to keep intact.
        summary_prefix: Prefix for the summary placeholder.

    Returns:
        Compacted list of history messages.
    """
    if len(history_items) <= max_items:
        return history_items

    # Keep the most recent messages, summarize the rest
    older = history_items[:-max_items]
    recent = history_items[-max_items:]

    # Count older messages by role
    role_counts: dict[str, int] = {}
    for item in older:
        if isinstance(item, dict):
            role = item.get("role", "unknown")
        else:
            role = getattr(item, "role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1

    summary_parts = [f"{count} {role} message(s)" for role, count in role_counts.items()]
    summary_text = summary_prefix + ", ".join(summary_parts)

    # Create a single summary item (using a simple dict-like structure)
    # Compatible with both HistoryMessage objects and plain dicts
    summary_item = type(older[0]) if older else dict
    try:
        compacted = [summary_item(role="system", content=summary_text)]
    except Exception:
        compacted = [{"role": "system", "content": summary_text}]

    compacted.extend(recent)
    return compacted
