"""Model-profile inference for protocol selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..harness._presets import known_family_presets


@dataclass(frozen=True)
class ModelProfile:
    id: str
    model_matchers: tuple[str, ...]
    default_protocol: str
    fallback_protocols: tuple[str, ...] = field(default_factory=tuple)
    tool_schema_style: str = "react"
    notes: str = ""


def _tool_schema_style(default_protocol: str) -> str:
    value = str(default_protocol or "").strip().lower()
    if value == "minimax_tool_call_v1":
        return "minimax"
    if "xml" in value:
        return "xml"
    if "json" in value:
        return "json"
    return "react"


def _build_profiles() -> tuple[ModelProfile, ...]:
    profiles = []
    for preset in known_family_presets():
        profiles.append(
            ModelProfile(
                id=f"{preset.id}_default",
                model_matchers=tuple(preset.model_matchers),
                default_protocol=preset.default_protocol,
                fallback_protocols=tuple(preset.fallback_protocols),
                tool_schema_style=_tool_schema_style(preset.default_protocol),
                notes=preset.notes,
            )
        )
    return tuple(profiles)


_PROFILES: tuple[ModelProfile, ...] = _build_profiles()


def _normalize(model_name: Optional[str]) -> str:
    return str(model_name or "").strip().lower()


def infer_model_profile(model_name: Optional[str]) -> Optional[ModelProfile]:
    normalized = _normalize(model_name)
    if not normalized:
        return None
    for profile in _PROFILES:
        if any(
            normalized.startswith(prefix) or prefix in normalized
            for prefix in profile.model_matchers
        ):
            return profile
    return None


def infer_default_protocol(
    model_name: Optional[str], *, fallback: str = "react_text_v1"
) -> str:
    profile = infer_model_profile(model_name)
    if profile is None:
        return fallback
    return profile.default_protocol


def known_model_profiles() -> Iterable[ModelProfile]:
    return _PROFILES


__all__ = [
    "ModelProfile",
    "infer_model_profile",
    "infer_default_protocol",
    "known_model_profiles",
]
