"""Preset-backed model harness helpers."""

from __future__ import annotations

from typing import Any

from ._adapters import OpenAICompatibleAdapter, adapter_for_kind
from ._presets import known_family_presets, resolve_builtin_preset
from ._types import (
    ContextPolicy,
    FamilyPreset,
    HarnessPolicy,
    ModelAdapter,
    ToolPolicy,
    build_protocol_for_preset,
)


def resolve_family_preset(identifier: str | None = None, *, family_id: str | None = None) -> FamilyPreset:
    target = family_id if family_id is not None else identifier
    return resolve_builtin_preset(target)


def build_harness_policy(
    *,
    model_name: str | None = None,
    family_id: str | None = None,
    protocol: Any = None,
    tool_delivery: str | None = None,
    resolution_source: str = "family_preset",
) -> HarnessPolicy:
    preset = resolve_family_preset(model_name, family_id=family_id)
    adapter = adapter_for_kind(preset.adapter_kind)
    protocol_obj = build_protocol_for_preset(
        preset=preset,
        protocol=protocol,
        delivery=tool_delivery,
    )
    parser = protocol_obj.parser_factory()
    return HarnessPolicy(
        family_preset=preset,
        adapter=adapter,
        protocol=protocol_obj,
        parser=parser,
        tool_policy=preset.tool_policy,
        context_policy=preset.context_policy,
        resolution_source=resolution_source,
    )


def build_model_for_preset(
    *,
    model_name: str,
    family_id: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    protocol: Any = None,
    tool_delivery: str | None = None,
    temperature: float | None = 0.2,
    max_tokens: int = 2048,
    timeout: int = 120,
    system_prompt: str | None = None,
    context_window: int | None = None,
    default_request_kwargs: dict[str, Any] | None = None,
) -> Any:
    harness = build_harness_policy(
        model_name=model_name,
        family_id=family_id,
        protocol=protocol,
        tool_delivery=tool_delivery,
    )
    # Merge preset-level recommended_request_kwargs with caller-provided kwargs.
    # Caller kwargs take precedence over preset recommendations.
    preset_kwargs = harness.family_preset.recommended_request_kwargs
    effective_kwargs = default_request_kwargs
    if preset_kwargs:
        if effective_kwargs is None:
            effective_kwargs = dict(preset_kwargs)
        else:
            merged = dict(preset_kwargs)
            merged.update(effective_kwargs)
            effective_kwargs = merged

    llm = harness.adapter.build_model(
        preset=harness.family_preset,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        context_policy=harness.context_policy,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        system_prompt=system_prompt,
        context_window=context_window,
        default_request_kwargs=effective_kwargs,
    )
    metadata = dict(getattr(llm, "qitos_harness_metadata", {}) or {})
    metadata.update(harness.to_dict())
    metadata.setdefault(
        "decision_lane_preference",
        "native_tool_calls"
        if harness.tool_policy.native_tool_call_preferred
        else "parser",
    )
    metadata.setdefault(
        "native_tool_call_preferred", harness.tool_policy.native_tool_call_preferred
    )
    metadata.setdefault("effective_tool_delivery", harness.protocol.tool_schema_delivery)
    setattr(llm, "qitos_harness_metadata", metadata)
    setattr(llm, "qitos_family_preset", harness.family_preset.id)
    setattr(llm, "qitos_protocol", harness.protocol.id)
    return llm


__all__ = [
    "ModelAdapter",
    "OpenAICompatibleAdapter",
    "ToolPolicy",
    "ContextPolicy",
    "HarnessPolicy",
    "FamilyPreset",
    "resolve_family_preset",
    "build_model_for_preset",
    "build_harness_policy",
    "known_family_presets",
]
