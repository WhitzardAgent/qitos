"""Built-in model-family presets."""

from __future__ import annotations

from typing import Iterable

from ._types import ContextPolicy, FamilyPreset, ToolPolicy


_PRESETS: tuple[FamilyPreset, ...] = (
    FamilyPreset(
        id="qwen",
        display_name="Qwen",
        model_matchers=("qwen", "tongyi"),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("xml_decision_v1", "react_text_v1"),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
            native_tool_call_preferred=True,
            notes="Prefer OpenAI-compatible tool parameters and use native tool calls when the backend returns them.",
        ),
        context_policy=ContextPolicy(
            context_window_hint=128_000,
            fallback_context_window=128_000,
            notes="Use explicit provider settings when available, otherwise default to a conservative 128k window.",
        ),
        notes="Research default for Qwen served through OpenAI-compatible endpoints, with native tool calls preferred before text parsing.",
        recommended_models=("Qwen/Qwen3-8B", "qwen-plus", "Qwen/Qwen3-32B"),
    ),
    FamilyPreset(
        id="kimi",
        display_name="Kimi",
        model_matchers=("kimi", "moonshot", "k2"),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("react_text_v1",),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
        ),
        context_policy=ContextPolicy(
            context_window_hint=128_000,
            fallback_context_window=128_000,
        ),
        notes="Moonshot/Kimi models keep the JSON-first harness with light fallback to ReAct text.",
        recommended_models=("kimi-k2-0905-preview", "moonshot-v1-128k"),
    ),
    FamilyPreset(
        id="minimax",
        display_name="MiniMax",
        model_matchers=("minimax-", "minimax/", "m2.5", "abab"),
        adapter_kind="openai-compatible",
        default_protocol="minimax_tool_call_v1",
        fallback_protocols=("terminus_xml_v1", "terminus_json_v1", "json_decision_v1"),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
            native_tool_call_preferred=True,
            notes="Keep native tool-call markup whenever the backend preserves it.",
        ),
        context_policy=ContextPolicy(
            context_window_hint=128_000,
            fallback_context_window=128_000,
        ),
        notes="MiniMax keeps its protocol-specific parser and fallback chain.",
        recommended_models=("MiniMax-M2.5",),
    ),
    FamilyPreset(
        id="gpt-oss",
        display_name="OpenAI gpt-oss",
        model_matchers=("gpt-oss",),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("react_text_v1",),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
        ),
        context_policy=ContextPolicy(
            context_window_hint=128_000,
            fallback_context_window=128_000,
        ),
        notes="Targets open-weight or third-party OpenAI-compatible serving, not OpenAI-hosted API access.",
        recommended_models=("gpt-oss-120b", "gpt-oss-20b"),
    ),
    FamilyPreset(
        id="gemma-4",
        display_name="Gemma 4",
        model_matchers=("gemma-4", "gemma4"),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("react_text_v1",),
        tool_policy=ToolPolicy(
            primary_delivery="prompt_injection",
            fallback_delivery="hybrid",
            notes="Prompt-driven tool rendering stays the safest default for served Gemma 4.",
        ),
        context_policy=ContextPolicy(
            context_window_hint=128_000,
            fallback_context_window=128_000,
        ),
        notes="OpenAI-compatible serving path for Gemma 4 without binding to the Gemini SDK.",
        recommended_models=("gemma-4-31b-it", "gemma-4-26b-a4b-it"),
    ),
    FamilyPreset(
        id="openai",
        display_name="OpenAI",
        model_matchers=("gpt-4.1", "gpt-4o", "o3", "o4-mini", "chatgpt-4o"),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("react_text_v1",),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
        ),
        context_policy=ContextPolicy(context_window_hint=128_000),
        notes="Compatibility preset retained for existing model-profile inference.",
        recommended_models=("gpt-4.1", "gpt-4o"),
    ),
    FamilyPreset(
        id="anthropic",
        display_name="Anthropic",
        model_matchers=("claude-",),
        adapter_kind="openai-compatible",
        default_protocol="react_text_v1",
        fallback_protocols=("json_decision_v1",),
        tool_policy=ToolPolicy(
            primary_delivery="prompt_injection",
            fallback_delivery="api_parameter",
        ),
        context_policy=ContextPolicy(context_window_hint=200_000),
        notes="Compatibility preset retained for profile inference; native adapters can be added later.",
        recommended_models=("claude-sonnet-4-5",),
    ),
    FamilyPreset(
        id="gemini",
        display_name="Gemini",
        model_matchers=("gemini-",),
        adapter_kind="openai-compatible",
        default_protocol="xml_decision_v1",
        fallback_protocols=("json_decision_v1", "react_text_v1"),
        tool_policy=ToolPolicy(
            primary_delivery="prompt_injection",
            fallback_delivery="api_parameter",
        ),
        context_policy=ContextPolicy(context_window_hint=1_048_576),
        notes="Compatibility preset retained for profile inference; served Gemma 4 uses a separate preset.",
        recommended_models=("gemini-2.5-pro",),
    ),
    FamilyPreset(
        id="deepseek",
        display_name="DeepSeek",
        model_matchers=("deepseek", "ds-v4", "ds-v3", "deepseek-"),
        adapter_kind="openai-compatible",
        default_protocol="json_decision_v1",
        fallback_protocols=("tool_use_xml_v1", "react_text_v1"),
        tool_policy=ToolPolicy(
            primary_delivery="api_parameter",
            fallback_delivery="prompt_injection",
        ),
        context_policy=ContextPolicy(context_window_hint=128_000),
        notes="DeepSeek models work best with JSON decision protocol, falling back to tool_use_xml for models that emit XML.",
        recommended_models=("ds-v4-pro", "ds-v4-flash", "deepseek-chat"),
    ),
    FamilyPreset(
        id="glm",
        display_name="GLM",
        model_matchers=("glm", "chatglm"),
        adapter_kind="openai-compatible",
        default_protocol="tool_use_xml_v1",
        fallback_protocols=("xml_decision_v1", "json_decision_v1", "react_text_v1"),
        tool_policy=ToolPolicy(
            primary_delivery="prompt_injection",
            fallback_delivery="api_parameter",
        ),
        context_policy=ContextPolicy(context_window_hint=128_000),
        notes="GLM models emit <tool_use> XML format. Use tool_use_xml_v1 protocol.",
        recommended_models=("glm5.1-w4a8-4maas", "glm-4"),
    ),
)


def known_family_presets() -> Iterable[FamilyPreset]:
    return _PRESETS


def resolve_builtin_preset(value: str | None) -> FamilyPreset:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("A model family or model name is required to resolve a preset.")
    for preset in _PRESETS:
        if preset.matches(normalized):
            return preset
    raise ValueError(f"Unknown QitOS model family preset: {value}")
