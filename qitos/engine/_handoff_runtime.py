"""Internal runtime for executing handoff decisions within the Engine loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .states import RuntimePhase

if TYPE_CHECKING:
    from .engine import Engine
    from ..core.decision import Decision
    from ..core.state import StateSchema

StateT = TypeVar("StateT")
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


@dataclass
class HandoffResult:
    """Result of a handoff execution."""

    from_agent: str
    to_agent: str


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

        target_name = decision.meta.get("handoff_target")
        if not target_name:
            raise ValueError("handoff decision requires meta['handoff_target']")

        if self.engine.agent_registry is None:
            raise ValueError("handoff requires agent_registry on Engine")

        spec = self.engine.agent_registry.resolve(target_name)

        # 1. State compatibility check / adaptation
        self._validate_state_compatibility(state, spec)

        # 2. Apply HandoffContext if present
        self._apply_handoff_context(state, spec)

        # 3. Swap agent
        old_agent_name = self.engine.agent.name
        self.engine.agent = spec.agent
        self.engine.tool_registry = spec.agent.tool_registry
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

        # 4. Reset protocol and prompt cache so the new agent builds fresh ones
        self.engine._resolved_protocol = self.engine.resolve_protocol()
        self.engine._last_system_prompt = ""
        self.engine._last_prompt_metadata = {}

        # 5. Record agent_id on step
        record.agent_id = target_name

        # 6. Emit trace events
        self.engine._emit(
            record.step_id,
            RuntimePhase.HANDOFF_START,
            payload={"from": old_agent_name, "to": target_name},
        )
        self.engine._emit(
            record.step_id,
            RuntimePhase.HANDOFF_END,
            payload={"agent": target_name},
        )

        return HandoffResult(from_agent=old_agent_name, to_agent=target_name)

    def _validate_state_compatibility(self, state: Any, spec: Any) -> None:
        """Check that state is compatible with the new agent.

        If a StateAdapter is provided on the spec, it will be used to adapt
        the state. Otherwise, we do a lightweight type check.
        """
        if spec.state_adapter is not None:
            adapted = spec.state_adapter.adapt(state)
            # Replace state in-place so the engine's reference stays valid
            if adapted is not state:
                state.__dict__.update(adapted.__dict__)
        # Without an adapter, we trust that the new agent can handle the
        # existing state type. A type mismatch will surface as an error
        # when the new agent's reduce() or build_system_prompt() is called.

    def _apply_handoff_context(self, state: Any, spec: Any) -> None:
        """Apply HandoffContext settings: shared_state_fields and max_history_rounds."""
        from ..core.agent_spec import HandoffContext

        hc: HandoffContext | None = spec.handoff_context
        if hc is None:
            return

        # Truncate history if max_history_rounds is set
        if hc.max_history_rounds is not None:
            history = self.engine._runtime_history
            if hasattr(history, "truncate_to_rounds"):
                history.truncate_to_rounds(hc.max_history_rounds)
            elif hasattr(history, "_items"):
                # Simple window history: keep last N*2 items (each round = thought+action)
                max_items = hc.max_history_rounds * 2
                history._items = history._items[-max_items:]

        # Filter state fields if shared_state_fields is specified
        if hc.shared_state_fields:
            # Only keep specified fields + base StateSchema fields
            from ..core.state import StateSchema
            base_fields = {"schema_version", "task", "current_step", "max_steps",
                           "final_result", "stop_reason", "metadata", "metrics"}
            allowed = base_fields | set(hc.shared_state_fields)
            for key in list(state.__dict__.keys()):
                if key not in allowed:
                    # Store removed fields in metadata for potential recovery
                    state.metadata.setdefault("_handoff_removed_fields", {})[key] = state.__dict__.pop(key)
