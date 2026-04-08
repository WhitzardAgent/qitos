"""Public harness and preset types for model-family switching."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from ..protocols import ModelProtocol, require_protocol


@dataclass(frozen=True)
class ToolPolicy:
    """How tool schemas should be delivered to the model transport."""

    primary_delivery: str = "prompt_injection"
    fallback_delivery: str = "prompt_injection"
    native_tool_call_preferred: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_delivery": self.primary_delivery,
            "fallback_delivery": self.fallback_delivery,
            "native_tool_call_preferred": self.native_tool_call_preferred,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ContextPolicy:
    """Stable, preset-level context defaults."""

    context_window_hint: Optional[int] = None
    fallback_context_window: int = 128_000
    use_compact_history: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_window_hint": self.context_window_hint,
            "fallback_context_window": self.fallback_context_window,
            "use_compact_history": self.use_compact_history,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class FamilyPreset:
    """Research defaults for one model family."""

    id: str
    display_name: str
    model_matchers: tuple[str, ...]
    adapter_kind: str
    default_protocol: str
    fallback_protocols: tuple[str, ...] = field(default_factory=tuple)
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    notes: str = ""
    recommended_models: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, value: str | None) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return False
        if normalized == self.id.lower():
            return True
        return any(
            normalized.startswith(pattern) or pattern in normalized
            for pattern in self.model_matchers
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "model_matchers": list(self.model_matchers),
            "adapter_kind": self.adapter_kind,
            "default_protocol": self.default_protocol,
            "fallback_protocols": list(self.fallback_protocols),
            "tool_policy": self.tool_policy.to_dict(),
            "context_policy": self.context_policy.to_dict(),
            "notes": self.notes,
            "recommended_models": list(self.recommended_models),
        }


class ModelAdapter:
    """Construct provider transports from a family preset."""

    kind: str = "base"

    def build_model(self, **kwargs: Any) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class HarnessPolicy:
    """Resolved harness policy for one concrete model run."""

    family_preset: FamilyPreset
    adapter: ModelAdapter
    protocol: ModelProtocol
    parser: Any
    tool_policy: ToolPolicy
    context_policy: ContextPolicy
    resolution_source: str = "family_preset"

    @property
    def parser_name(self) -> str:
        return self.parser.__class__.__name__

    def protocol_with_delivery(self, delivery: str | None = None) -> ModelProtocol:
        effective_delivery = str(delivery or self.tool_policy.primary_delivery)
        return replace(
            self.protocol,
            tool_schema_delivery=effective_delivery,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_preset": self.family_preset.id,
            "adapter_kind": self.adapter.kind,
            "protocol": self.protocol.id,
            "fallback_protocols": list(self.protocol.fallback_protocols),
            "parser": self.parser_name,
            "tool_policy": self.tool_policy.to_dict(),
            "context_policy": self.context_policy.to_dict(),
            "resolution_source": self.resolution_source,
        }


def build_protocol_for_preset(
    *,
    preset: FamilyPreset,
    protocol: str | ModelProtocol | None = None,
    delivery: str | None = None,
) -> ModelProtocol:
    base = require_protocol(protocol or preset.default_protocol)
    fallback_protocols = (
        tuple(base.fallback_protocols)
        if protocol is not None
        else tuple(preset.fallback_protocols or base.fallback_protocols)
    )
    return replace(
        base,
        tool_schema_delivery=str(delivery or preset.tool_policy.primary_delivery),
        fallback_protocols=fallback_protocols,
    )


Resolver = Callable[[str | None], FamilyPreset]
