from __future__ import annotations

import base64
import sys
from types import ModuleType
from types import SimpleNamespace

from qitos.models import (
    AnthropicModel,
    GeminiModel,
    LiteLLMModel,
    LMStudioModel,
    ModelFactory,
    OpenAICompatibleModel,
    OllamaModel,
    infer_context_window,
)


class _FakeHTTPResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"status={self.status_code}")
            error.response = self
            raise error


def test_anthropic_native_messages_adapter(monkeypatch) -> None:
    captured = {}

    # Ensure the default Anthropic endpoint is used, even if ANTHROPIC_BASE_URL
    # is set in the environment (e.g., a corporate proxy).
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeHTTPResponse(
            {
                "content": [
                    {"type": "text", "text": "Final Answer: native anthropic works"}
                ],
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 3,
                    "output_tokens": 22,
                },
            }
        )

    monkeypatch.setattr("qitos.models.anthropic.requests.post", fake_post)
    llm = AnthropicModel(api_key="anthropic-test", model="claude-test")
    out = llm(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Say hello."},
        ]
    )

    assert out == "Final Answer: native anthropic works"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "anthropic-test"
    assert captured["json"]["system"] == "You are helpful."
    assert captured["json"]["messages"] == [{"role": "user", "content": "Say hello."}]
    assert llm.extract_usage() == {
        "prompt_tokens": 103,
        "completion_tokens": 22,
        "total_tokens": 125,
    }


def test_gemini_native_adapter(monkeypatch) -> None:
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _FakeHTTPResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": 'Action: search(query="gemini")'},
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 18,
                    "candidatesTokenCount": 7,
                    "totalTokenCount": 25,
                },
            }
        )

    monkeypatch.setattr("qitos.models.gemini.requests.post", fake_post)
    llm = GeminiModel(api_key="gemini-test", model="gemini-2.5-flash")
    out = llm(
        [
            {"role": "system", "content": "Follow protocol."},
            {"role": "user", "content": "Search for docs."},
            {"role": "assistant", "content": "Thinking."},
        ]
    )

    assert out == 'Action: search(query="gemini")'
    assert captured["params"] == {"key": "gemini-test"}
    assert (
        captured["json"]["systemInstruction"]["parts"][0]["text"] == "Follow protocol."
    )
    assert captured["json"]["contents"][0]["role"] == "user"
    assert captured["json"]["contents"][1]["role"] == "model"
    assert llm.extract_usage() == {
        "prompt_tokens": 18,
        "completion_tokens": 7,
        "total_tokens": 25,
    }


def test_litellm_adapter_and_usage(monkeypatch) -> None:
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "Final Answer: litellm works"}}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
        }

    monkeypatch.setitem(
        sys.modules, "litellm", SimpleNamespace(completion=fake_completion)
    )
    llm = LiteLLMModel(model="anthropic/claude-3-5-sonnet-latest", api_key="lite-key")
    out = llm([{"role": "user", "content": "Say hi"}])

    assert out == "Final Answer: litellm works"
    assert captured["model"] == "anthropic/claude-3-5-sonnet-latest"
    assert captured["api_key"] == "lite-key"
    assert llm.extract_usage() == {
        "prompt_tokens": 13,
        "completion_tokens": 5,
        "total_tokens": 18,
    }


def test_model_factory_from_env_supports_new_providers(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("LM_STUDIO_BASE_URL", raising=False)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env")
    llm = ModelFactory.from_env(model="claude-test")
    assert isinstance(llm, AnthropicModel)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-env")
    llm = ModelFactory.from_env(model="gemini-test")
    assert isinstance(llm, GeminiModel)

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LITELLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("LITELLM_API_KEY", "lite-env")
    llm = ModelFactory.from_env()
    assert isinstance(llm, LiteLLMModel)
    assert llm.model == "openai/gpt-4o-mini"

    monkeypatch.setenv("QITOS_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    llm = ModelFactory.from_env(model="llama3.1")
    assert isinstance(llm, OllamaModel)

    monkeypatch.setenv("QITOS_MODEL_PROVIDER", "lmstudio")
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    llm = ModelFactory.from_env(model="local-model")
    assert isinstance(llm, LMStudioModel)


def test_local_openai_compatible_like_parsing_supports_tool_calls() -> None:
    lmstudio = LMStudioModel(model="local-model")
    out = lmstudio._parse_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search",
                                    "arguments": '{"query": "lmstudio"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert out == 'Action: search(query="lmstudio")'

    ollama = OllamaModel(model="llama3.1")
    out = ollama._parse_response(
        {
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "grep_files",
                            "arguments": {"pattern": "TODO"},
                        }
                    }
                ]
            }
        }
    )
    assert out == 'Action: grep_files(pattern="TODO")'


def test_context_registry_infers_anthropic_and_gemini_windows() -> None:
    assert infer_context_window("claude-3-5-sonnet-latest") == 200_000
    assert infer_context_window("gemini-2.5-flash") == 1_048_576


def test_openai_compatible_model_formats_multimodal_chat_messages(tmp_path, monkeypatch) -> None:
    captured = {}
    png_path = tmp_path / "shot.png"
    png_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn2gbcAAAAASUVORK5CYII="
        )
    )

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Final Answer: visual ok", tool_calls=None
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=11, completion_tokens=5, total_tokens=16
                ),
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: _FakeClient(**kwargs), APIError=Exception)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.test/v1",
    )
    out = llm(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this screenshot."},
                    {"type": "image_file", "path": str(png_path), "detail": "high"},
                ],
            }
        ]
    )

    assert out == "Final Answer: visual ok"
    message = captured["messages"][0]
    assert message["role"] == "user"
    assert isinstance(message["content"], list)
    assert message["content"][0] == {"type": "text", "text": "Inspect this screenshot."}
    image_block = message["content"][1]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["detail"] == "high"
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_compatible_model_uses_instance_inference_key(monkeypatch) -> None:
    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Final Answer: routed", tool_calls=None
                        )
                    )
                ],
                usage=None,
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: _FakeClient(**kwargs), APIError=Exception)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("QITOS_INFERENCE_KEY", "env-key")

    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.test/v1",
        inference_key="instance-key",
    )
    out = llm([{"role": "user", "content": "Route me"}])

    assert out == "Final Answer: routed"
    assert captured["client_kwargs"]["default_headers"] == {
        "x-inspire-inference-key": "instance-key"
    }


def test_openai_compatible_model_falls_back_to_env_inference_key(monkeypatch) -> None:
    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Final Answer: env routed", tool_calls=None
                        )
                    )
                ],
                usage=None,
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: _FakeClient(**kwargs), APIError=Exception)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("QITOS_INFERENCE_KEY", "env-key")

    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.test/v1",
    )
    out = llm([{"role": "user", "content": "Route me"}])

    assert out == "Final Answer: env routed"
    assert captured["client_kwargs"]["default_headers"] == {
        "x-inspire-inference-key": "env-key"
    }


def test_openai_compatible_model_retries_and_uses_120s_timeout(monkeypatch) -> None:
    captured = {"attempts": 0, "client_kwargs": None}

    class _TransientError(Exception):
        pass

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["attempts"] += 1
            if captured["attempts"] < 3:
                raise _TransientError("request time out")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Final Answer: retried ok", tool_calls=None
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=9, completion_tokens=4, total_tokens=13
                ),
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_openai = ModuleType("openai")
    fake_openai.OpenAI = lambda **kwargs: _FakeClient(**kwargs)
    fake_openai.APIError = _TransientError
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr("qitos.models.openai.time.sleep", lambda _: None)

    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.test/v1",
    )
    out = llm([{"role": "user", "content": "Retry please"}])

    assert out == "Final Answer: retried ok"
    assert captured["attempts"] == 3
    assert captured["client_kwargs"]["timeout"] == 120
    assert llm.timeout == 120


def test_openai_compatible_model_call_raw_retries_on_transient_errors(monkeypatch) -> None:
    captured = {"attempts": 0}

    class _TransientError(Exception):
        pass

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["attempts"] += 1
            if captured["attempts"] < 3:
                raise _TransientError("request time out")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Final Answer: raw retried ok", tool_calls=None
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=7, completion_tokens=3, total_tokens=10
                ),
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    fake_openai = ModuleType("openai")
    fake_openai.OpenAI = lambda **kwargs: _FakeClient(**kwargs)
    fake_openai.APIError = _TransientError
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr("qitos.models.openai.time.sleep", lambda _: None)

    llm = OpenAICompatibleModel(
        model="gpt-4.1-mini",
        api_key="test-key",
        base_url="https://example.test/v1",
    )
    response = llm.call_raw([{"role": "user", "content": "Retry raw please"}])

    assert captured["attempts"] == 3
    assert response.choices[0].message.content == "Final Answer: raw retried ok"


def test_explicit_provider_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("QITOS_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
    llm = ModelFactory.from_env(model="claude-test")
    assert isinstance(llm, AnthropicModel)

    monkeypatch.setenv("QITOS_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-env")
    llm = ModelFactory.from_env(model="gemini-test")
    assert isinstance(llm, GeminiModel)

    monkeypatch.delenv("QITOS_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "LITELLM_MODEL",
        "LITELLM_API_KEY",
        "OLLAMA_HOST",
        "OLLAMA_BASE_URL",
        "LM_STUDIO_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert ModelFactory.from_env() is None
