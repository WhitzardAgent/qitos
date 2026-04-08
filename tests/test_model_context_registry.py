from __future__ import annotations

from qitos.models import OpenAIModel, OpenAICompatibleModel, infer_context_window


def test_infer_context_window_for_common_models() -> None:
    assert infer_context_window("gpt-4.1") == 1_047_576
    assert infer_context_window("gpt-4o-mini-2024-07-18") == 128_000
    assert infer_context_window("o3-mini") == 200_000
    assert infer_context_window("gpt-3.5-turbo") == 16_385
    assert infer_context_window("Qwen/Qwen3-8B") == 128_000
    assert infer_context_window("kimi-k2-0905-preview") == 128_000
    assert infer_context_window("MiniMax-M2.5") == 128_000
    assert infer_context_window("gpt-oss-120b") == 128_000
    assert infer_context_window("gemma-4-31b-it") == 128_000
    assert infer_context_window("unknown-model", fallback=99_999) == 99_999


def test_model_defaults_to_registry_inferred_context_window() -> None:
    llm = OpenAIModel(model="gpt-4o-mini", api_key="test-key")
    assert llm.context_window == 128_000


def test_explicit_context_window_still_wins() -> None:
    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        context_window=32_000,
    )
    assert llm.context_window == 32_000
