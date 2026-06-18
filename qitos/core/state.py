"""Typed state schema and migration utilities for QitOS kernel."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, ClassVar, Dict, Optional, Tuple, Type, TypeVar

from .errors import StopReason


MigrationFn = Callable[[Dict[str, Any]], Dict[str, Any]]
StateT = TypeVar("StateT", bound="StateSchema")


class StateValidationError(ValueError):
    """Raised when state validation fails."""


class StateMigrationError(ValueError):
    """Raised when state migration fails."""


class StateMigrationRegistry:
    """Simple in-process migration graph for state schema versions."""

    def __init__(self):
        self._migrations: Dict[Tuple[int, int], MigrationFn] = {}

    def register(self, from_version: int, to_version: int, fn: MigrationFn) -> None:
        if to_version <= from_version:
            raise StateMigrationError("to_version must be greater than from_version")
        self._migrations[(from_version, to_version)] = fn

    def migrate(
        self, payload: Dict[str, Any], from_version: int, to_version: int
    ) -> Dict[str, Any]:
        if from_version == to_version:
            return payload

        current = from_version
        output = dict(payload)

        while current < to_version:
            step = (current, current + 1)
            if step not in self._migrations:
                raise StateMigrationError(
                    f"Missing migration path from v{current} to v{current + 1}"
                )
            output = self._migrations[step](output)
            current += 1

        return output


@dataclass
class StateSchema:
    """Canonical typed state base for AgentModule."""

    schema_version: int = 1
    task: str = ""
    current_step: int = 0
    max_steps: int = 10
    final_result: Optional[str] = None
    stop_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)

    # Subclasses can override this and set their own registry.
    migration_registry: ClassVar[StateMigrationRegistry] = StateMigrationRegistry()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls: Type[StateT], payload: Dict[str, Any], strict: bool = True
    ) -> StateT:
        from dataclasses import fields

        known_fields = {f.name for f in fields(cls)}
        unknown_fields = [k for k in payload.keys() if k not in known_fields]

        if strict and unknown_fields:
            raise StateValidationError(f"Unknown state fields: {unknown_fields}")

        filtered = {k: v for k, v in payload.items() if k in known_fields}

        obj = cls(**filtered)
        obj.validate()
        return obj

    @classmethod
    def migrate_payload(
        cls, payload: Dict[str, Any], target_version: int
    ) -> Dict[str, Any]:
        from_version = int(payload.get("schema_version", 1))
        migrated = cls.migration_registry.migrate(payload, from_version, target_version)
        migrated["schema_version"] = target_version
        return migrated

    def validate(self) -> None:
        if not isinstance(self.task, str):
            raise StateValidationError("task must be a string")
        if self.current_step < 0:
            raise StateValidationError("current_step must be >= 0")
        if self.max_steps <= 0:
            raise StateValidationError("max_steps must be > 0")
        if self.current_step > self.max_steps:
            raise StateValidationError("current_step cannot exceed max_steps")
        if self.final_result is not None and not isinstance(self.final_result, str):
            raise StateValidationError("final_result must be a string when provided")
        if self.stop_reason is not None:
            try:
                StopReason(str(self.stop_reason))
            except ValueError as exc:
                raise StateValidationError(
                    f"invalid stop_reason: {self.stop_reason}"
                ) from exc

    def set_stop(
        self, reason: StopReason | str, final_result: Optional[str] = None
    ) -> None:
        if isinstance(reason, StopReason):
            self.stop_reason = reason.value
        else:
            self.stop_reason = StopReason(str(reason)).value
        if final_result is not None:
            self.final_result = final_result

    @classmethod
    def _get_field_reducers(cls) -> Any:
        """Get the FieldReducerRegistry for this StateSchema subclass.

        Lazily scans Annotated[type, reducer] annotations on first access.
        Uses __dict__ lookup on the class to ensure per-subclass caching.
        """
        # Check this specific class's own __dict__ (not inherited)
        if "_field_reducers_cache" not in cls.__dict__:
            from .field_reducers import FieldReducerRegistry
            setattr(cls, "_field_reducers_cache", FieldReducerRegistry.from_schema(cls))
        return cls.__dict__["_field_reducers_cache"]

    def reduce_update(self, update: Dict[str, Any]) -> None:
        """Apply per-field reducers to update state fields in-place.

        For fields annotated with Annotated[type, reducer], the reducer
        function is called with (current_value, update_value).
        Unannotated fields use Replace (last-value-wins) semantics,
        which is identical to the old __dict__.update() behavior.
        """
        registry = self._get_field_reducers()
        reduced = registry.apply_all(self, update)
        for key, value in reduced.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def advance_step(self) -> None:
        self.current_step += 1
        self.validate()
