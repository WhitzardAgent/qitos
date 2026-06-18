"""RunState serialization and schema versioning.

Borrowed from OpenAI Agents RunState design:
- references/openai-agents-python/src/agents/run_state.py
- Schema versioning with fail-fast forward compatibility
- Context serialization metadata for type recovery
- Round-trip via to_json() / from_json()
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, cast

from ..core.state import StateMigrationError

# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

CURRENT_SCHEMA_VERSION = "1.0"

SCHEMA_VERSION_SUMMARIES: Dict[str, str] = {
    "1.0": "Initial RunState snapshot format for Engine pause/resume.",
}

SUPPORTED_SCHEMA_VERSIONS = frozenset(SCHEMA_VERSION_SUMMARIES)


# ---------------------------------------------------------------------------
# RunState — serializable snapshot of an Engine run
# ---------------------------------------------------------------------------

@dataclass
class RunState:
    """Serializable snapshot of an Engine run's state.

    Can be persisted to disk/database and later restored to resume
    the run or inspect its history.
    """

    schema_version: str = CURRENT_SCHEMA_VERSION
    agent_name: str = ""
    step: int = 0
    task_text: str = ""
    state_data: Dict[str, Any] = field(default_factory=dict)
    state_type: str = ""
    records: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    checkpoint_id: Optional[str] = None
    trace_state: Optional[Dict[str, Any]] = None
    context_metadata: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    token_usage: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ---- serialization metadata ----

    _serialization_meta: Dict[str, Any] = field(default_factory=lambda: {
        "original_type": "none",
        "serialized_via": "none",
        "requires_deserializer": False,
    })

    # ---- construction from EngineResult ----

    @classmethod
    def from_engine_result(
        cls,
        result: Any,
        agent_name: str = "",
        checkpoint_id: Optional[str] = None,
    ) -> RunState:
        """Create a RunState from an EngineResult.

        Args:
            result: An EngineResult instance.
            agent_name: Name of the agent that produced the result.
            checkpoint_id: Optional checkpoint ID for resume.
        """
        state = result.state
        state_data = state.to_dict() if hasattr(state, "to_dict") else {}
        state_type = (
            f"{type(state).__module__}.{type(state).__qualname__}"
            if state is not None
            else ""
        )

        records = []
        for r in getattr(result, "records", []):
            records.append(asdict(r) if hasattr(r, "__dataclass_fields__") else {})

        events = []
        for e in getattr(result, "events", []):
            events.append(asdict(e) if hasattr(e, "__dataclass_fields__") else {})

        budget_obj = getattr(result, "budget", None)
        budget_data = (
            asdict(cast(Any, budget_obj))
            if is_dataclass(budget_obj) and not isinstance(budget_obj, type)
            else {}
        )

        return cls(
            agent_name=agent_name,
            step=getattr(result, "step_count", 0),
            task_text=getattr(state, "task", ""),
            state_data=state_data,
            state_type=state_type,
            records=records,
            events=events,
            checkpoint_id=checkpoint_id,
            budget=budget_data,
            token_usage=getattr(result, "total_tokens", 0),
            _serialization_meta={
                "original_type": "state_dataclass" if state_type else "none",
                "serialized_via": "to_dict",
                "requires_deserializer": False,
            },
        )

    # ---- JSON serialization ----

    def to_json(
        self,
        *,
        pretty: bool = True,
        context_serializer: Optional[Callable[[Any], Any]] = None,
    ) -> str:
        """Serialize to a JSON string.

        Args:
            pretty: If True, produce pretty-printed JSON.
            context_serializer: Optional function to serialize
                non-standard context types.

        Returns:
            JSON string representation.
        """
        payload = self._to_serializable_dict(context_serializer=context_serializer)
        return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)

    @classmethod
    def from_json(
        cls,
        raw: str,
        *,
        context_deserializer: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> RunState:
        """Deserialize from a JSON string.

        Args:
            raw: JSON string.
            context_deserializer: Optional function to rebuild
                custom context types.

        Returns:
            Restored RunState.

        Raises:
            StateMigrationError: If the schema version is newer than
                the current version (fail-fast forward compatibility).
        """
        payload = json.loads(raw)
        return cls._from_serializable_dict(
            payload, context_deserializer=context_deserializer
        )

    # ---- internal helpers ----

    def _to_serializable_dict(
        self,
        *,
        context_serializer: Optional[Callable[[Any], Any]] = None,
    ) -> Dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        payload: Dict[str, Any] = {
            "$schemaVersion": self.schema_version,
            "agent_name": self.agent_name,
            "step": self.step,
            "task_text": self.task_text,
            "state_data": self.state_data,
            "state_type": self.state_type,
            "records": self.records,
            "events": self.events,
            "checkpoint_id": self.checkpoint_id,
            "trace_state": self.trace_state,
            "context_metadata": self.context_metadata,
            "budget": self.budget,
            "token_usage": self.token_usage,
            "created_at": self.created_at,
            "_serialization_meta": self._serialization_meta,
        }
        if context_serializer is not None:
            payload["state_data"] = context_serializer(self.state_data)
            payload["_serialization_meta"]["serialized_via"] = "context_serializer"
            payload["_serialization_meta"]["requires_deserializer"] = True
        return payload

    @classmethod
    def _from_serializable_dict(
        cls,
        payload: Dict[str, Any],
        *,
        context_deserializer: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> RunState:
        """Restore from a serialized dict."""
        version = payload.get("$schemaVersion", "1.0")

        # Fail-fast forward compatibility
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise StateMigrationError(
                f"Unsupported RunState schema version: {version}. "
                f"Current version: {CURRENT_SCHEMA_VERSION}. "
                f"Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # Apply migrations if needed (currently only v1.0)
        # Future: apply RUN_STATE_MIGRATIONS from older versions

        state_data = payload.get("state_data", {})
        if context_deserializer is not None:
            state_data = context_deserializer(state_data)

        return cls(
            schema_version=CURRENT_SCHEMA_VERSION,
            agent_name=payload.get("agent_name", ""),
            step=payload.get("step", 0),
            task_text=payload.get("task_text", ""),
            state_data=state_data,
            state_type=payload.get("state_type", ""),
            records=payload.get("records", []),
            events=payload.get("events", []),
            checkpoint_id=payload.get("checkpoint_id"),
            trace_state=payload.get("trace_state"),
            context_metadata=payload.get("context_metadata", {}),
            budget=payload.get("budget", {}),
            token_usage=payload.get("token_usage", 0),
            created_at=payload.get("created_at", ""),
            _serialization_meta=payload.get("_serialization_meta", {}),
        )


__all__ = [
    "RunState",
    "CURRENT_SCHEMA_VERSION",
    "SCHEMA_VERSION_SUMMARIES",
    "SUPPORTED_SCHEMA_VERSIONS",
]
