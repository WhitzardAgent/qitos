"""Model transport adapters used by harness presets."""

from __future__ import annotations

from ..models.context_registry import infer_context_window
from ..models.openai import OpenAICompatibleModel
from ._types import ContextPolicy, FamilyPreset, ModelAdapter


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return float(default)


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    return int(default)


def resolve_context_window(
    model_name: str | None, *, context_policy: ContextPolicy, explicit: int | None = None
) -> int:
    if isinstance(explicit, int) and explicit > 0:
        return int(explicit)
    inferred = infer_context_window(
        model_name,
        fallback=context_policy.context_window_hint
        or context_policy.fallback_context_window,
    )
    if isinstance(inferred, int) and inferred > 0:
        return inferred
    return int(context_policy.fallback_context_window)


class OpenAICompatibleAdapter(ModelAdapter):
    kind = "openai-compatible"

    def build_model(self, **kwargs: object) -> OpenAICompatibleModel:
        preset = kwargs["preset"]
        model_name = kwargs["model_name"]
        api_key = kwargs.get("api_key")
        base_url = kwargs.get("base_url")
        context_policy = kwargs["context_policy"]
        temperature = _coerce_float(kwargs.get("temperature"), 0.2)
        max_tokens = _coerce_int(kwargs.get("max_tokens"), 2048)
        timeout = _coerce_int(kwargs.get("timeout"), 60)
        system_prompt = kwargs.get("system_prompt")
        context_window = kwargs.get("context_window")
        if not isinstance(preset, FamilyPreset):
            raise TypeError("preset must be a FamilyPreset")
        if not isinstance(model_name, str):
            raise TypeError("model_name must be a string")
        if not isinstance(context_policy, ContextPolicy):
            raise TypeError("context_policy must be a ContextPolicy")
        llm = OpenAICompatibleModel(
            model=model_name,
            api_key=str(api_key) if api_key is not None else None,
            base_url=str(base_url) if base_url is not None else None,
            system_prompt=str(system_prompt) if system_prompt is not None else None,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            context_window=resolve_context_window(
                model_name,
                context_policy=context_policy,
                explicit=(
                    _coerce_int(context_window, 0)
                    if context_window is not None
                    else None
                ),
            ),
        )
        setattr(
            llm,
            "qitos_harness_metadata",
            {
                "family_preset": preset.id,
                "context_policy": context_policy.to_dict(),
                "adapter_kind": self.kind,
            },
        )
        return llm


def adapter_for_kind(kind: str) -> ModelAdapter:
    normalized = str(kind or "").strip().lower()
    if normalized == "openai-compatible":
        return OpenAICompatibleAdapter()
    raise ValueError(f"Unknown harness adapter kind: {kind}")
